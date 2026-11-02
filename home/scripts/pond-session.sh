# pond-session: the lifecycle of the Pond session's own processes.
#
#   start            reap what a dead Pond session left behind, claim the
#                    notification name, then start the shell, the launcher and
#                    the lock guard, each under a supervisor
#   stop             stop this session's supervisors, daemons and lockers
#   restart-shell    stop this session's shell; its supervisor starts it again
#   lock [reason]    lock the screen; exit 0 only once it is confirmed locked
#                    (below), 3 if locked by a lock screen it cannot confirm
#   suspend          lock, then suspend; refuses to suspend an unlocked session
#   launcher ARGS    run `vicinae ARGS` against this session's own server
#   notifications-allowed
#                    exit 1 while a live Pond session owns notifications (the
#                    ExecCondition of swaync.service, home/hyprland/pond.nix)
#
# The body of the pond-session wrapper in home/hyprland/pond.nix, which sets
# POND_SHELL, POND_LOCK, POND_LAUNCHER and POND_PAM_SERVICE and puts the tools
# below on PATH. home/scripts/test-pond-session.sh runs it against stand-ins.
#
# Ownership is by registration, never by name or environment. Every process
# this script starts is recorded under $XDG_RUNTIME_DIR/pond-session/<HYPRLAND_
# INSTANCE_SIGNATURE>/procs/<role> as "<pid> <start time>", and only a process
# whose /proc start time still matches is ever signalled, so a reused pid is
# never hit. Roles: shell, launcher, guard (their supervisors are supervise-*),
# lock (Pond's locker) and fallback-lock (hyprlock). Stopping a role also stops
# what that process itself started and still parents (Vicinae's helpers), but
# never another registered process, and applications are left alone: Vicinae
# and Quickshell launch them detached. A session is stale once its compositor
# is gone, so a second, live Hyprland of the same user (another VT) is never
# touched, nor are the Waybar and Caelestia sessions, which register nothing.
#
# Locking. All entry points (Super+Ctrl+L, the shell's idle timer and settings
# buttons through settings/session.py, logind's Lock signal and lock before
# sleep through the guard) end in `pond-session lock`, serialized by a lock
# file. Success (exit 0) means a locker this script started, identified by pid
# and start time, has itself been sent ext-session-lock's `locked`: Pond's
# locker answers isSecure, or hyprlock logged it (confirm_once). Never a
# surviving process, an accepted IPC call, `hyprctl locked`, or a record.
#
# Hyprland 0.56 (src/managers/SessionLockManager.cpp, protocols/SessionLock.cpp)
# denies a new lock while the session is locked unless
# misc:allow_session_lock_restore is set, which this script never does. So
# while a confirmed client holds the lock nobody else can, and of two lockers
# of ours the confirmed one is the holder and the other may be killed. With
# the option set, a new lock would replace the old one without telling it, and
# the old client's later unlock_and_destroy would unlock the whole session
# under the new lock screen; and Hyprland has no way to ask which client holds
# a lock, or whether its holder is dead (hl.clear_crashed_lockscreen force-
# unlocks). So a lock is never taken over:
#
#  - not locked: start Pond's locker (or ask the running one to lock); if it
#    has not confirmed after POND_SECURE_TIMEOUT, start hyprlock as well and
#    take whichever confirms first. Starting a locker is always safe: it gets
#    the lock or is denied it. One that never confirms is not killed, since it
#    may hold the lock; `lock` fails (1) and the next call checks it again.
#  - locked, and no locker of ours confirms within POND_SECURE_TIMEOUT: the
#    holder is someone else's lock screen, or a dead one (Hyprland's
#    "lockdead" screen). It is left alone and `lock` exits 3, not 0.
#
# Every call, IPC and wait shares one deadline (LOCK_BUDGET, queueing
# included), which fits logind's delay-inhibitor budget.
#
# No errexit: the supervisor loops and the signal helpers depend on non-zero
# statuses.

sig=${HYPRLAND_INSTANCE_SIGNATURE:-}
runtime=${XDG_RUNTIME_DIR:-}
cmd=${1:-}
self=$0
[[ $self == /* ]] || self=$(command -v -- "$self" || printf '%s' "$self")
state=$runtime/pond-session
claims=$state/claims

# Bounds, in seconds. The whole of `lock`, fallback included, fits in
# logind's delay-inhibitor budget (InhibitDelayMaxSec in
# modules/desktop-hyprland.nix), or suspend would go ahead regardless.
LOCK_BUDGET=${POND_LOCK_BUDGET:-12}     # all of `lock`
POND_SECURE=${POND_SECURE_TIMEOUT:-5}   # Pond's locker reaching isSecure
IPC_TIMEOUT=${POND_IPC_TIMEOUT:-1.5}    # one `qs ipc` call
CTL_TIMEOUT=${POND_CTL_TIMEOUT:-2}      # hyprctl, systemctl, gdbus, notify-send
GRACE=${POND_TERM_GRACE:-2}             # TERM to KILL

log() {
  logger -t pond-session -- "$*" 2>/dev/null || true
  printf 'pond-session: %s\n' "$*" >&2
}

notify() { # <urgency> <summary> <body>
  timeout "$CTL_TIMEOUT" notify-send -u "$1" -a Pond -- "$2" "$3" >/dev/null 2>&1 || true
}
# The same, without waiting for it: `lock` must end within its budget.
notify_async() {
  (
    close_fds
    notify "$@"
  ) &
  disown "$!" 2>/dev/null
}

# Close every inherited descriptor above stderr: a background process must
# not keep a serialization lock or the guard's inhibitor pipe open (that would
# hold off other callers, or sleep, for good).
close_fds() {
  local fd
  for fd in "/proc/$BASHPID/fd/"*; do
    fd=${fd##*/}
    ((fd > 2)) && exec {fd}>&-
  done
}

# Milliseconds since the epoch, into the variable named $1, without a fork.
now_ms() {
  local t=${EPOCHREALTIME//[!0-9]/}
  printf -v "$1" '%d' $((10#$t / 1000))
}
ms() { # seconds (may be fractional) -> milliseconds, into $2
  local s=$1 whole frac
  whole=${s%%.*}
  frac=${s#"$whole"}
  frac=${frac#.}000
  printf -v "$2" '%d' $((10#${whole:-0} * 1000 + 10#${frac:0:3}))
}
# Remaining seconds until the deadline (ms) in $1, capped at $2, as "s.mmm".
remaining() {
  local deadline=$1 cap=$2 now left capms
  now_ms now
  left=$((deadline - now))
  ms "$cap" capms
  ((left < capms)) || left=$capms
  ((left > 0)) || left=0
  printf '%d.%03d' $((left / 1000)) $((left % 1000))
}
before() { # deadline-ms: true while it has not passed
  local now
  now_ms now
  ((now < $1))
}

# --- /proc ---------------------------------------------------------------

# read_stat <pid>: sets st_state, st_ppid, st_start. Shell builtins only.
read_stat() {
  local line rest
  local -a f
  read -r line 2>/dev/null <"/proc/$1/stat" || return 1
  rest=${line##*) }
  read -ra f <<<"$rest"
  st_state=${f[0]} st_ppid=${f[1]} st_start=${f[19]}
}

# Running, sleeping or stopped, but not a zombie waiting to be reaped.
is_live() {
  read_stat "$1" || return 1
  [[ $st_state != Z && $st_state != X ]]
}

# same_proc <pid> <start>: the very process that was registered.
same_proc() {
  is_live "$1" && [[ $st_start == "$2" ]]
}

# A compositor instance is alive when its lock file names a running Hyprland.
# The comm check guards against the pid having been reused.
compositor_alive() {
  local lock=$runtime/hypr/$1/hyprland.lock pid comm
  [[ -n $1 && -r $lock ]] || return 1
  read -r pid <"$lock" || return 1
  [[ $pid =~ ^[0-9]+$ ]] || return 1
  is_live "$pid" || return 1
  read -r comm 2>/dev/null <"/proc/$pid/comm" || return 1
  [[ ${comm,,} == *hyprland* ]]
}

other_compositor_alive() {
  local dir id
  for dir in "$runtime"/hypr/*/; do
    [[ -d $dir ]] || continue
    id=${dir%/}
    id=${id##*/}
    [[ $id != "$sig" ]] || continue
    compositor_alive "$id" && return 0
  done
  return 1
}

# --- registry --------------------------------------------------------------

# Every function below takes the session directory first, so a stale
# session's registry is handled exactly like this one's.
sessdir() { printf '%s/%s' "$state" "$1"; }

register() { # <dir> <role> <pid>
  local d=$1/procs
  read_stat "$3" || return 1
  mkdir -p "$d" || return 1
  printf '%s %s\n' "$3" "$st_start" >"$d/.$2.$$" && mv -f "$d/.$2.$$" "$d/$2"
}

# registered <dir> <role>: prints "<pid> <start>" if that process still runs.
registered() {
  local pid start
  read -r pid start 2>/dev/null <"$1/procs/$2" || return 1
  [[ $pid =~ ^[0-9]+$ && -n $start ]] || return 1
  same_proc "$pid" "$start" || return 1
  printf '%s %s\n' "$pid" "$start"
}

unregister() { # <dir> <role> [pid]: only if still that pid
  local pid _
  if [[ -n ${3:-} ]]; then
    read -r pid _ 2>/dev/null <"$1/procs/$2" || return 0
    [[ $pid == "$3" ]] || return 0
  fi
  rm -f "$1/procs/$2"
}

roles_of() { # <dir>: every recorded role
  local f
  for f in "$1"/procs/*; do
    [[ -e $f ]] && printf '%s\n' "${f##*/}"
  done
}

# terminate <dir> <role>...: TERM the registered processes of these roles,
# then whatever they started and still parent, then KILL what is left after
# the grace period. Other registered processes, and everything below them,
# are never touched (a lock started from the shell is not the shell's).
terminate() {
  local dir=$1 role pid start p
  shift
  local -a leaders=() starts=() kids_pids=() kids_starts=() queue
  local -A child_of=() exclude=() mine=() role_pid=()
  for role in "$@"; do
    read -r pid start < <(registered "$dir" "$role") || {
      unregister "$dir" "$role"
      continue
    }
    leaders+=("$pid")
    starts+=("$start")
    mine[$pid]=1
    role_pid[$role]=$pid
  done
  [[ ${#leaders[@]} -gt 0 ]] || return 0
  while read -r role; do
    read -r pid _ < <(registered "$dir" "$role") || continue
    [[ -n ${mine[$pid]:-} ]] || exclude[$pid]=1
  done < <(roles_of "$dir")
  # Nor this command and the processes it runs under (a terminal opened from
  # the shell may be the one asking to restart it).
  p=$BASHPID
  while [[ $p -gt 1 ]] && read_stat "$p"; do
    [[ -n ${mine[$p]:-} ]] || exclude[$p]=1
    p=$st_ppid
  done
  for p in /proc/[0-9]*; do
    p=${p#/proc/}
    read_stat "$p" || continue
    child_of[$st_ppid]+=" $p"
  done
  queue=("${leaders[@]}")
  while [[ ${#queue[@]} -gt 0 ]]; do
    p=${queue[0]}
    queue=("${queue[@]:1}")
    for pid in ${child_of[$p]:-}; do
      [[ -z ${exclude[$pid]:-} && -z ${mine[$pid]:-} ]] || continue
      read_stat "$pid" || continue
      mine[$pid]=1
      kids_pids+=("$pid")
      kids_starts+=("$st_start")
      queue+=("$pid")
    done
  done
  signal_all TERM leaders starts
  wait_gone leaders starts 1
  signal_all TERM kids_pids kids_starts
  wait_gone leaders starts "$GRACE"
  wait_gone kids_pids kids_starts 0.5
  if ! all_gone leaders starts || ! all_gone kids_pids kids_starts; then
    log "escalating to SIGKILL in ${dir##*/}: roles $*"
    signal_all KILL leaders starts
    signal_all KILL kids_pids kids_starts
    wait_gone leaders starts 1
  fi
  # By pid: a supervisor may already have registered the next instance.
  for role in "${!role_pid[@]}"; do unregister "$dir" "$role" "${role_pid[$role]}"; done
}

signal_all() { # <signal> <pids-array-name> <starts-array-name>
  local -n _p=$2 _s=$3
  local i
  for i in "${!_p[@]}"; do
    same_proc "${_p[$i]}" "${_s[$i]}" && kill "-$1" "${_p[$i]}" 2>/dev/null
  done
  return 0
}
all_gone() {
  local -n _p=$1 _s=$2
  local i
  for i in "${!_p[@]}"; do
    same_proc "${_p[$i]}" "${_s[$i]}" && return 1
  done
  return 0
}
wait_gone() { # <pids> <starts> <seconds>
  local deadline t
  now_ms deadline
  ms "$3" t
  deadline=$((deadline + t))
  while ! all_gone "$1" "$2"; do
    before "$deadline" || return 1
    sleep 0.05
  done
}

# spawn <dir> <role> <log> <command...>: start a process in its own session,
# detached from the caller, with no inherited descriptors (close_fds), and
# register it. An @ID@ in <log> becomes "<pid>.<start time>" of the process
# itself, so that file is opened by that one process and no other. setsid
# does not fork here (the subshell does not lead its process group), so the
# pid is the command's.
spawn() {
  local dir=$1 role=$2 out=$3 pid
  shift 3
  (
    close_fds
    [[ $out != *@ID@* ]] || ! read_stat "$BASHPID" || out=${out//@ID@/$BASHPID.$st_start}
    exec setsid "$@" </dev/null >>"$out" 2>&1
  ) &
  pid=$!
  disown "$pid" 2>/dev/null
  register "$dir" "$role" "$pid" || {
    log "could not register $role ($pid)"
    return 1
  }
  spawned=$pid
}


# with_lock <file> <seconds> <command...>: run under an exclusive flock.
with_lock() {
  local file=$1 wait=$2 fd rc
  shift 2
  exec {fd}>>"$file" || return 75
  if ! flock -w "$wait" "$fd"; then
    exec {fd}>&-
    log "timed out waiting for ${file##*/}"
    return 75
  fi
  "$@"
  rc=$?
  exec {fd}>&-
  return "$rc"
}

require_session() {
  if [[ -z $sig || -z $runtime ]]; then
    log "needs HYPRLAND_INSTANCE_SIGNATURE and XDG_RUNTIME_DIR (run it from the Pond session)"
    exit 2
  fi
  if [[ ! $sig =~ ^[A-Za-z0-9_.-]+$ ]]; then
    log "unexpected HYPRLAND_INSTANCE_SIGNATURE: $sig"
    exit 2
  fi
  dir=$(sessdir "$sig")
  mkdir -p "$dir/procs" "$claims" && chmod 700 "$state" || exit 2
}

# --- claims: notification name and Vicinae's endpoint ------------------------
#
# One user D-Bus and one $XDG_RUNTIME_DIR/vicinae serve every session of the
# user, so each can belong to one Pond session at a time. A claim is the
# owning session's signature; a claim whose compositor is gone is void.

claim_owner() { # <name>: prints the live owner, if any
  local owner
  read -r owner 2>/dev/null <"$claims/$1" || return 1
  [[ -n $owner ]] && compositor_alive "$owner" || return 1
  printf '%s\n' "$owner"
}
set_claim() { printf '%s\n' "$sig" >"$claims/.$1.$$" && mv -f "$claims/.$1.$$" "$claims/$1"; }
release_claim() { # <name> <owner>
  local owner
  read -r owner 2>/dev/null <"$claims/$1" || return 0
  [[ $owner == "$2" ]] && rm -f "$claims/$1"
  return 0
}

# swaync is D-Bus-activatable for org.freedesktop.Notifications (it is the
# Waybar session's notification daemon), so any notification sent while
# Pond's shell is starting or restarting would start it, and it would keep the
# name from Pond for good. Pond claims the name, which makes swaync's unit
# skip itself (`notifications-allowed`), and stops it. If another session is
# live and swaync is serving it, that session keeps its notifications and
# Pond goes without: the bus has room for one notification daemon.
claim_notifications() {
  local owner
  if owner=$(claim_owner notifications) && [[ $owner != "$sig" ]]; then
    log "notifications belong to the Pond session $owner"
    return 0
  fi
  if other_compositor_alive &&
    timeout "$CTL_TIMEOUT" "${POND_SYSTEMCTL:-systemctl}" --user is-active --quiet swaync.service; then
    log "another session's swaync owns notifications; Pond will not receive them"
    notify normal "Notifications" "Another desktop session is showing notifications, so Pond will not."
    return 0
  fi
  set_claim notifications
  timeout "$CTL_TIMEOUT" "${POND_SYSTEMCTL:-systemctl}" --user stop swaync.service 2>/dev/null
  return 0
}

notifications_allowed() {
  [[ -n $runtime ]] || exit 0
  claim_owner notifications >/dev/null && exit 1
  exit 0
}

# Vicinae 0.23.2 always listens on $XDG_RUNTIME_DIR/vicinae/vicinae.sock and
# keeps its helpers' pid files next to it, killing the previous helper on
# start. A second server would steal the first session's helpers, and the
# second session's binds would open the first session's window on the other
# compositor. Moving the socket means changing XDG_RUNTIME_DIR, which the
# applications Vicinae launches inherit (Wayland, PipeWire and D-Bus sockets
# all live there), so a second simultaneous Pond session gets no launcher, says
# so, and never replaces the first one's.
claim_launcher() {
  local owner
  if owner=$(claim_owner launcher) && [[ $owner != "$sig" ]]; then
    printf 'is serving the Pond session on another VT'
    return 1
  fi
  if [[ -z ${owner:-} ]] && timeout "$CTL_TIMEOUT" "$POND_LAUNCHER" ping >/dev/null 2>&1; then
    printf 'was started outside Pond and is still running'
    return 1
  fi
  set_claim launcher
}

launcher_cmd() {
  require_session
  local owner
  owner=$(claim_owner launcher) || owner=
  if [[ $owner == "$sig" ]] && registered "$dir" launcher >/dev/null; then
    exec "$POND_LAUNCHER" "$@"
  fi
  log "launcher unavailable in this session (owner: ${owner:-none})"
  notify normal "Launcher unavailable" \
    "Vicinae is serving another Pond session. Close that session to use it here."
  exit 1
}

# --- supervision -----------------------------------------------------------

reap_session() { # <dir> <owner-signature>
  local roles=() role
  terminate "$1" supervise-shell supervise-launcher supervise-guard
  while read -r role; do roles+=("$role"); done < <(roles_of "$1")
  [[ ${#roles[@]} -eq 0 ]] || terminate "$1" "${roles[@]}"
  release_claim notifications "$2"
  release_claim launcher "$2"
}

reap_stale() {
  local d id
  for d in "$state"/*/; do
    [[ -d $d/procs ]] || continue
    d=${d%/}
    id=${d##*/}
    [[ $id != "$sig" ]] || continue
    compositor_alive "$id" && continue
    reap_session "$d" "$id"
    rm -rf -- "$d"
  done
}

ensure_supervisor() { # <role>
  registered "$dir" "supervise-$1" >/dev/null && return 0
  spawn "$dir" "supervise-$1" /dev/null "$self" supervise "$1" "$sig"
}

# Run one daemon of the session and start it again when it exits. Gives up
# after five starts in a row that each lasted under ten seconds. Polls the
# compositor while the daemon runs: when it is gone, the daemon and the rest of
# the session are stopped even if Hyprland's shutdown hook never ran.
supervise() {
  local role=$1 child=0 stopping=0 gone=0 fails=0 started reason wfd
  local -a argv
  sig=${2:-}
  dir=$(sessdir "$sig")
  [[ -d $dir/procs ]] || exit 2
  case $role in
    shell) argv=("$POND_SHELL") ;;
    launcher) argv=("$POND_LAUNCHER" server) ;;
    guard) argv=("$self" guard-run "$sig") ;;
    *) log "unknown role $role"; exit 2 ;;
  esac
  export POND_SESSION_BIN=$self
  unset POND_SESSION_ID POND_ROLE
  trap 'stopping=1' TERM INT HUP
  # A fifo nobody writes to: `read -t` on it is a sleep without a fork that a
  # trapped signal interrupts.
  mkfifo "$dir/.wait.$$" 2>/dev/null
  exec {wfd}<>"$dir/.wait.$$"
  rm -f "$dir/.wait.$$"
  while ((stopping == 0)); do
    if ! compositor_alive "$sig"; then gone=1; break; fi
    if [[ $role == launcher ]] && ! reason=$(with_lock "$claims/.lock" 5 claim_launcher); then
      log "not starting Vicinae: it $reason"
      notify normal "Launcher unavailable" "Vicinae $reason; this session runs without it."
      break
    fi
    started=$SECONDS
    "${argv[@]}" </dev/null {wfd}>&- &
    child=$!
    register "$dir" "$role" "$child"
    while ((stopping == 0)) && is_live "$child"; do
      if ! compositor_alive "$sig"; then gone=1; break; fi
      read -r -t 1 -u "$wfd" _
    done
    if ((stopping || gone)); then
      terminate "$dir" "$role"
      break
    fi
    wait "$child" 2>/dev/null
    unregister "$dir" "$role" "$child"
    if ((SECONDS - started < 10)); then fails=$((fails + 1)); else fails=0; fi
    if ((fails >= 5)); then
      log "$role exited 5 times within 10s of starting; giving up (pond-session restart-shell tries again)"
      [[ $role != guard ]] || notify critical "Lock before sleep is off" \
        "Pond's lock guard keeps failing; suspending will not lock the screen first."
      break
    fi
    log "$role exited; restarting"
    read -r -t 1 -u "$wfd" _
  done
  [[ $role != launcher ]] || release_claim launcher "$sig"
  if ((gone)); then
    log "compositor gone; stopping the rest of session $sig"
    local roles=() r
    # Daemons and lockers only: the other supervisors notice on their own,
    # and stopping one another would only race.
    while read -r r; do
      [[ $r == supervise-* ]] || roles+=("$r")
    done < <(roles_of "$dir")
    [[ ${#roles[@]} -eq 0 ]] || terminate "$dir" "${roles[@]}"
    release_claim notifications "$sig"
    release_claim launcher "$sig"
  fi
  unregister "$dir" "supervise-$role" "$$"
  exit 0
}

# --- locking ---------------------------------------------------------------

# Timeout for one control call, into stdout: <cap> (CTL_TIMEOUT by default),
# or what is left of the lock deadline if that is less. Fails once the
# deadline has passed, so no call starts after it.
ctl_t() { # [cap]
  local cap=${1:-$CTL_TIMEOUT} t
  if [[ -z ${deadline:-} ]]; then
    printf '%s' "$cap"
    return 0
  fi
  t=$(remaining "$deadline" "$cap")
  [[ $t != 0.000 ]] || return 1
  printf '%s' "$t"
}

# Bounded calls are KILLed on time: nothing past the deadline waits on a TERM.
# --foreground, or timeout would KILL its own process group, itself included
# (the IPC client is `qs` itself: the package's wrappers exec it).
#
# compositor_locked: 0 locked, 1 not locked, 2 unknown (hyprctl failed, or no
# time was left to ask).
compositor_locked() {
  local out t
  t=$(ctl_t) || return 2
  out=$(HYPRLAND_INSTANCE_SIGNATURE=$sig timeout --foreground -s KILL "$t" hyprctl locked 2>/dev/null) || return 2
  case $out in
    true) return 0 ;;
    false) return 1 ;;
  esac
  return 2
}

lockctl() { # <pid> <method>: one bounded IPC call to that very locker
  local t rc
  t=$(ctl_t "$IPC_TIMEOUT") || return 1
  POND_PAM_SERVICE=$POND_PAM_SERVICE timeout --foreground -s KILL "$t" \
    "$POND_LOCK" ipc --pid "$1" call lockscreen "$2" 2>/dev/null
  rc=$?
  ((rc != 124 && rc != 137)) || pond_hung=1
  return "$rc"
}

# Confirmation, for each locker this script starts:
#  - Pond's locker answers isSecure over IPC addressed to its pid. Quickshell
#    sets that on ext-session-lock's `locked` and clears it on `finished` and
#    on unlock (src/wayland/session_lock/lock.cpp).
#  - hyprlock (0.9.5) logs "onLockLocked called" from its `locked` handler, and
#    hyprutils' logger flushes stdout after each line. Its stdout is a file
#    named after its own pid and start time, created by that process as it
#    starts (spawn), so the line can only be its own. Logging
#    "onLockFinished called" (denied) or "Unlocked, exiting!" undoes it.
pond_secure() {
  local answer rc
  answer=$(lockctl "$1" isSecure)
  rc=$?
  # Command substitution runs lockctl in a subshell; propagate its timeout
  # here so a hung IPC does not delay checking the fallback on every pass.
  ((rc != 124 && rc != 137)) || pond_hung=1
  [[ $rc == 0 && $answer == true ]]
}

hyprlock_confirmed() { # <pid> <start>
  local log=$dir/hyprlock.$1.$2.log line locked=1
  same_proc "$1" "$2" && [[ -r $log ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    case $line in
      *"onLockLocked called"*) locked=0 ;;
      *"onLockFinished called"* | *"Unlocked, exiting!"*) return 1 ;;
    esac
  done <"$log"
  ((locked == 0)) && same_proc "$1" "$2"
}

start_pond() {
  : >"$dir/lock.log"
  POND_PAM_SERVICE=$POND_PAM_SERVICE spawn "$dir" lock "$dir/lock.log" "$POND_LOCK" || return 1
  pond_pid=$spawned pond_start=$st_start
}

start_hyprlock() {
  command -v hyprlock >/dev/null || {
    log "hyprlock is not installed"
    return 1
  }
  rm -f "$dir"/hyprlock.*.log
  # --immediate-render: lock without first gathering background images.
  spawn "$dir" fallback-lock "$dir/hyprlock.@ID@.log" hyprlock --immediate-render || return 1
  hl_pid=$spawned hl_start=$st_start
}

kill_role() { # <role>: SIGKILL, no grace, for a locker known not to hold the
  # lock. A locker is never asked to exit, because asking may unlock.
  local pid start t
  read -r pid start < <(registered "$dir" "$1") || {
    unregister "$dir" "$1"
    return 0
  }
  kill -KILL "$pid" 2>/dev/null
  t=$(ctl_t 1) || t=0
  local -a p=("$pid") s=("$start")
  wait_gone p s "$t"
  unregister "$dir" "$1" "$pid"
}

# confirm_once: 0 if one of this script's live lockers is confirmed, 1 if
# not yet, 2 if none is left. The confirmed one is the lock's only holder (a
# held lock cannot be taken while the restore option is off), so the other is
# not, and is killed.
confirm_once() {
  if [[ -n $hl_pid ]] && ! same_proc "$hl_pid" "$hl_start"; then
    unregister "$dir" fallback-lock "$hl_pid"
    hl_pid=
  fi
  if [[ -n $pond_pid ]] && ! same_proc "$pond_pid" "$pond_start"; then
    unregister "$dir" lock "$pond_pid"
    pond_pid=
  fi
  [[ -n $hl_pid$pond_pid ]] || return 2
  if [[ -n $hl_pid ]] && hyprlock_confirmed "$hl_pid" "$hl_start"; then
    [[ -z $pond_pid ]] || kill_role lock
    log "locked with hyprlock"
    return 0
  fi
  # A hung Pond locker is asked again only once hyprlock is gone, so that its
  # timeouts do not delay noticing hyprlock's confirmation.
  if [[ -n $pond_pid ]] && { ((pond_hung == 0)) || [[ -z $hl_pid ]]; } &&
    pond_secure "$pond_pid"; then
    [[ -z $hl_pid ]] || kill_role fallback-lock
    return 0
  fi
  return 1
}

wait_confirmed() { # <until-ms>: confirm_once until <until> or the deadline
  local rc
  while before "$1" && before "$deadline"; do
    confirm_once
    rc=$?
    ((rc == 1)) || return "$rc"
    before "$1" && sleep 0.1
  done
  return 1
}

lock_session() {
  local reason=$1 locked pond_until secure_ms
  ms "$POND_SECURE" secure_ms
  now_ms pond_until
  pond_until=$((pond_until + secure_ms))
  pond_hung=0
  read -r pond_pid pond_start < <(registered "$dir" lock) || unregister "$dir" lock
  read -r hl_pid hl_start < <(registered "$dir" fallback-lock) || {
    unregister "$dir" fallback-lock
    rm -f "$dir"/hyprlock.*.log
  }

  # Already locked by one of ours, confirmed: done.
  if [[ -n $pond_pid$hl_pid ]] && before "$deadline"; then
    confirm_once && return 0
  fi

  compositor_locked
  locked=$?
  if ((locked == 0)); then
    # Nobody can take a held lock (the restore option stays off), so a new
    # locker would only be denied. One of ours that is alive but unconfirmed
    # may hold it and be about to confirm (Hyprland sends `locked` at most 5s
    # after the request); otherwise its holder is unknown.
    [[ -n $pond_pid$hl_pid ]] && wait_confirmed "$pond_until" && return 0
    lock_unverified "$reason"
    return 3
  fi
  # Not locked, or unknown: starting a locker is safe either way, since it
  # either gets the lock or is denied it.
  if [[ -n $pond_pid ]]; then
    lockctl "$pond_pid" lock >/dev/null
  elif before "$deadline"; then
    start_pond
  fi
  wait_confirmed "$pond_until" && return 0
  if [[ -z $hl_pid ]] && before "$deadline"; then
    log "Pond's lock screen did not confirm a secure lock; starting hyprlock as well"
    start_hyprlock
  fi
  wait_confirmed "$deadline" && return 0
  # Whatever of ours is still running is left alone: it may hold the lock and
  # confirm later, and the next `lock` checks it again.
  log "the session could NOT be locked ($reason): no lock screen confirmed a secure lock"
  notify_async critical "Screen not locked" \
    "Neither Pond's lock screen nor hyprlock confirmed a lock. The screen may be unlocked."
  return 1
}

lock_unverified() { # <reason>
  log "the session is locked ($1), but no lock screen pond-session started confirms it:" \
    "it is held by another lock screen, or by one that died. Hyprland cannot tell which," \
    "so it is not replaced. If no lock screen is shown, unlock from another VT (docs/pond-hyprland.md)."
  notify_async critical "Lock not confirmed" \
    "The session is locked, but not by a lock screen Pond started and can confirm. It was left in place."
}

# The deadline is set before waiting for a lock already in progress, so the
# whole call, queueing included, stays within LOCK_BUDGET. Exit status: 0
# confirmed by a locker of ours, 3 locked but not confirmably by us, 1 not
# confirmed (the screen may be unlocked).
lock_main() {
  require_session
  local budget_ms rc
  ms "$LOCK_BUDGET" budget_ms
  now_ms deadline
  deadline=$((deadline + budget_ms))
  with_lock "$dir/lock.serial" "$(remaining "$deadline" "$LOCK_BUDGET")" lock_session "${1:-manual}"
  rc=$?
  ((rc != 75)) || rc=1 # another lock attempt still holds the queue
  return "$rc"
}

suspend_main() {
  lock_main suspend || {
    log "not suspending: the session is not confirmed locked"
    exit 1
  }
  exec "${POND_SYSTEMCTL:-systemctl}" suspend
}

# --- lock guard ------------------------------------------------------------

# Holds logind's sleep delay inhibitor and answers PrepareForSleep and this
# session's Lock signal with `pond-session lock`. It is its own supervised
# process so that a crashed or restarting shell does not leave suspend
# unlocked. logind suspends anyway once InhibitDelayMaxSec has passed, so a
# failed lock cannot hold sleep off; it is retried as soon as the machine
# resumes, and reported.
inhibit_on() {
  [[ -z ${inh_pid:-} ]] || return 0
  rm -f "$dir/.inhibit"
  mkfifo "$dir/.inhibit" || return 1
  systemd-inhibit --what=sleep --mode=delay --who=Pond \
    --why="Lock the screen before sleep" cat <"$dir/.inhibit" >/dev/null 2>&1 &
  inh_pid=$!
  exec {inh_fd}>"$dir/.inhibit"
  rm -f "$dir/.inhibit"
  sleep 0.2
  if ! is_live "$inh_pid"; then
    log "logind refused the sleep delay inhibitor"
    exec {inh_fd}>&-
    wait "$inh_pid" 2>/dev/null
    inh_pid=
    return 1
  fi
}
inhibit_off() {
  [[ -n ${inh_pid:-} ]] || return 0
  exec {inh_fd}>&-
  local i
  for i in $(seq 20); do
    is_live "$inh_pid" || break
    sleep 0.05
  done
  kill -TERM "$inh_pid" 2>/dev/null
  wait "$inh_pid" 2>/dev/null
  inh_pid=
}

guard_run() {
  local path line failed=0 rc i
  sig=${1:-}
  require_session
  path=$(timeout "$CTL_TIMEOUT" gdbus call --system --dest org.freedesktop.login1 \
    --object-path /org/freedesktop/login1 --method org.freedesktop.login1.Manager.GetSession \
    "${XDG_SESSION_ID:-auto}" 2>/dev/null)
  path=${path#*\'}
  path=${path%%\'*}
  if [[ $path != /org/freedesktop/login1/session/* ]]; then
    log "could not find this login session in logind"
    exit 1
  fi
  exec {mon_fd}< <(exec gdbus monitor --system --dest org.freedesktop.login1 2>/dev/null)
  mon_pid=$!
  trap 'inhibit_off; kill "$mon_pid" 2>/dev/null; exit 0' TERM INT HUP
  inhibit_on || { kill "$mon_pid" 2>/dev/null; exit 1; }
  while read -r -u "$mon_fd" line; do
    case $line in
      "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (true,)")
        "$self" lock sleep
        rc=$?
        failed=$((rc != 0))
        ((rc == 0)) || log "suspending without a confirmed lock: logind does not wait any longer"
        inhibit_off
        ;;
      "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (false,)")
        inhibit_on
        if ((failed)); then
          for i in 1 2 3; do
            "$self" lock resume
            rc=$?
            # 3: locked, by a lock screen pond-session cannot confirm; asking
            # again changes nothing (lock_unverified has said so).
            ((rc != 0 && rc != 3)) || break
            sleep 2
          done
          ((rc == 0 || rc == 3)) || notify critical "Screen not locked" \
            "The screen was not locked before sleep and still could not be locked."
        fi
        ;;
      "$path: org.freedesktop.login1.Session.Lock ()")
        "$self" lock logind
        ;;
    esac
  done
  inhibit_off
  kill "$mon_pid" 2>/dev/null
  exit 1 # the monitor ended (logind restarted?): the supervisor starts a new guard
}

# --- commands --------------------------------------------------------------

start_main() {
  reap_stale
  with_lock "$claims/.lock" 5 claim_notifications
  ensure_supervisor shell
  ensure_supervisor launcher
  ensure_supervisor guard
}

stop_main() {
  # Supervisors first, so nothing is respawned; then whatever is left.
  terminate "$dir" supervise-shell supervise-launcher supervise-guard
  local roles=() role
  while read -r role; do roles+=("$role"); done < <(roles_of "$dir")
  [[ ${#roles[@]} -eq 0 ]] || terminate "$dir" "${roles[@]}"
  release_claim notifications "$sig"
  release_claim launcher "$sig"
}

restart_shell_main() {
  terminate "$dir" shell
  # A no-op while the supervisor lives; brings it back if it gave up.
  ensure_supervisor shell
}

case $cmd in
  start) require_session; with_lock "$dir/control" 30 start_main ;;
  stop) require_session; with_lock "$dir/control" 30 stop_main ;;
  restart-shell) require_session; with_lock "$dir/control" 30 restart_shell_main ;;
  lock) lock_main "${2:-manual}" ;;
  suspend) suspend_main ;;
  launcher) shift; launcher_cmd "$@" ;;
  notifications-allowed) notifications_allowed ;;
  supervise) supervise "${2:-}" "${3:-}" ;;
  guard-run) guard_run "${2:-}" ;;
  *)
    printf 'usage: pond-session start|stop|restart-shell|lock|suspend|launcher ARGS\n' >&2
    exit 2
    ;;
esac
