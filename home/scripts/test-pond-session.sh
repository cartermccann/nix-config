#!/usr/bin/env bash
# Behavioural test of pond-session.sh against stand-in processes.
#
#   bash home/scripts/test-pond-session.sh
#
# Everything happens under a temporary XDG_RUNTIME_DIR with stand-ins for
# Hyprland (a process plus a model of its session-lock rules, see `fakecomp`),
# hyprctl, Pond's locker, hyprlock, the shell, Vicinae, systemctl and
# notify-send, so it never touches the real session, the real systemd user
# manager or any real locker. Needs a real /proc, bash, coreutils and
# util-linux (setsid, flock, logger).
#
# The lock guard is tested against real gdbus and systemd-inhibit talking to a
# fake logind (fake-login1.py) on a private D-Bus. That part needs
#   POND_TEST_PYGI   a python3 with PyGObject (GI_TYPELIB_PATH set for it)
# and dbus-daemon, gdbus and systemd-inhibit on PATH; without them it is
# reported as SKIPPED and the run fails.
set -u

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
T=$(mktemp -d /tmp/pond-session-test.XXXXXX)
export POND_TEST_RUN=$T # inherited by every fake; the cleanup finds them by it
pass=0
fail=0
skipped=0

cleanup() {
  local dir pid entry
  [[ -z ${DBUS_PID:-} ]] || kill "$DBUS_PID" 2>/dev/null
  for dir in /proc/[0-9]*; do
    pid=${dir#/proc/}
    [[ $pid != "$$" ]] || continue
    while IFS= read -r -d '' entry; do
      if [[ $entry == "POND_TEST_RUN=$T" ]]; then
        kill -KILL "$pid" 2>/dev/null
        break
      fi
    done 2>/dev/null <"$dir/environ"
  done
  rm -rf "$T"
}
trap cleanup EXIT

ok() { pass=$((pass + 1)); printf '  ok   %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }
wait_for() { # <seconds> '<condition, evaluated each tenth of a second>'
  local n=$(($1 * 10)) i
  for ((i = 0; i < n; i++)); do
    if eval "$2"; then return 0; fi
    sleep 0.1
  done
  return 1
}

BASH_BIN=$(command -v bash)
mkdir -p "$T/bin" "$T/run/hypr" "$T/comp"
chmod 700 "$T/run"
cp "$BASH_BIN" "$T/bin/Hyprland"
mkfifo "$T/fifo"
BLOCK="read -r <'$T/fifo'"

start_of() { # /proc start time of a pid
  local line rest
  local -a f
  read -r line 2>/dev/null <"/proc/$1/stat" || return 1
  rest=${line##*) }
  read -ra f <<<"$rest"
  [[ ${f[0]} != Z ]] || return 1
  printf '%s\n' "${f[19]}"
}
export -f start_of

# fakecomp: Hyprland's session-lock rules (SessionLockManager::onNewSessionLock
# and protocols/SessionLock.cpp, 0.56.1) for one compositor, in $T/comp/<sig>:
#   request <sig> <pid>   a client asks for the lock: granted | denied | ignored
#   confirm <sig> <pid>   the compositor sends `locked` to that client
#   unlock <sig> <pid>    that client sends unlock_and_destroy
#   locked <sig>          true | false (hyprctl locked: the protocol's m_locked)
#   restore <sig> true|false
#   owner <sig>           "<pid> <confirmed>" of the current lock, if any
# A denied lock object is inert. Every other one stays live ($c/live) until its
# client unlocks or dies, including one replaced through the restore option or
# ignored while another lock was in limbo, and unlock_and_destroy from any live
# one clears m_locked for the whole session, whoever holds the lock now. A
# client that dies without unlocking leaves the session locked with no owner
# ("lockdead"). Not modelled: Hyprland's 5s timer that sends `locked` to a lock
# still in limbo; a test sends it with `confirm` where it matters.
cat >"$T/bin/fakecomp" <<'EOF'
#!/usr/bin/env bash
c=$POND_TEST_RUN/comp/$2
mkdir -p "$c"
exec 9>>"$c/.flock"
flock 9
locked=$(cat "$c/locked" 2>/dev/null || echo false)
restore=$(cat "$c/restore" 2>/dev/null || echo false)
read -r opid ostart oconf 2>/dev/null <"$c/owner"
owner_alive() { [[ -n ${opid:-} ]] && [[ $(start_of "$opid" 2>/dev/null) == "$ostart" ]]; }
live_add() { echo "$1 $(start_of "$1")" >>"$c/live"; }
is_live_lock() { [[ -n $(start_of "$1" 2>/dev/null) ]] && grep -qx "$1 $(start_of "$1")" "$c/live" 2>/dev/null; }
case $1 in
  request)
    if [[ $locked == true && $restore != true ]]; then echo denied
    elif owner_alive && [[ $oconf != true ]]; then
      live_add "$3"; echo true >"$c/locked"; echo ignored
    else
      echo "$3 $(start_of "$3") false" >"$c/owner"
      live_add "$3"
      echo true >"$c/locked"
      echo granted
    fi ;;
  confirm) [[ ${opid:-} == "$3" ]] && echo "$opid $ostart true" >"$c/owner" ;;
  unlock)
    if is_live_lock "$3"; then
      echo false >"$c/locked"
      grep -vx "$3 $(start_of "$3")" "$c/live" >"$c/live.new"; mv "$c/live.new" "$c/live"
      [[ ${opid:-} != "$3" ]] || rm -f "$c/owner"
    fi ;;
  locked) echo "$locked" ;;
  restore) echo "$3" >"$c/restore"; echo "$3" >>"$c/restore.log" ;;
  owner) owner_alive && echo "$opid $oconf" ;;
esac
EOF

# hyprctl. Every call is recorded; $T/hyprctl.broken fails them all and
# $T/hyprctl.hang makes them hang.
cat >"$T/bin/hyprctl" <<'EOF'
#!/usr/bin/env bash
s=$HYPRLAND_INSTANCE_SIGNATURE
echo "$*" >>"$POND_TEST_RUN/hyprctl.calls"
[[ -e $POND_TEST_RUN/hyprctl.broken ]] && exit 1
[[ ! -e $POND_TEST_RUN/hyprctl.hang ]] || exec sleep 30
case $1 in
  locked) fakecomp locked "$s" ;;
  eval)
    case $2 in
      *allow_session_lock_restore\ =\ true*) fakecomp restore "$s" true ;;
      *allow_session_lock_restore\ =\ false*) fakecomp restore "$s" false ;;
    esac >/dev/null
    echo ok ;;
esac
EOF

# Pond's locker. Started without arguments it is the lock screen, which locks
# on load (after $T/lockdelay seconds, if that exists: QML still loading); its
# behaviour is $T/lockmode at start:
#   normal  takes the lock and is confirmed     never  never asks for the lock
#   limbo   asks, is never confirmed            crash  exits at once
#   hang    takes the lock, then IPC hangs
# Like Pond's real locker, it stays running after unlocking.
# `ipc --pid N call lockscreen lock|isSecure` answers for locker N.
cat >"$T/bin/lock-fake" <<'EOF'
#!/usr/bin/env bash
R=$POND_TEST_RUN
s=$HYPRLAND_INSTANCE_SIGNATURE
take() {
  case $(fakecomp request "$s" "$1") in
    granted)
      sleep 0.1
      [[ $(cat "$R/locker.$1.mode") != slow ]] || sleep 1.5
      [[ $(cat "$R/locker.$1.mode") == limbo ]] || fakecomp confirm "$s" "$1" ;;
  esac
}
if [[ $# -eq 0 ]]; then
  echo "$POND_PAM_SERVICE" >"$R/lock.pam"
  mode=$(cat "$R/lockmode" 2>/dev/null || echo normal)
  echo "$$" >>"$R/locker.starts"
  echo "$mode" >"$R/locker.$$.mode"
  [[ $mode != hang ]] || touch "$R/locker.$$.hung"
  case $mode in
    crash) exit 1 ;;
    never) ;;
    *) sleep "$(cat "$R/lockdelay" 2>/dev/null || echo 0)"; take "$$" ;;
  esac
  exec 0<"$R/fifo"
  read -r
  exit 0
fi
if [[ $1 == take ]]; then take "$2"; exit 0; fi
if [[ $1 == ipc && $2 == --pid ]]; then
  pid=$3 method=$6
  echo "$method $pid" >>"$R/ipc.calls"
  [[ -e $R/locker.$pid.mode && -n $(start_of "$pid" 2>/dev/null) ]] || { echo "No instance found for pid $pid" >&2; exit 255; }
  [[ ! -e $R/locker.$pid.hung ]] || exec sleep 30
  case $method in
    # Like the real one (Shell.lock() activates the session lock), the call
    # returns at once and the locker asks for the lock on its own.
    lock) [[ $(cat "$R/locker.$pid.mode") == never ]] || setsid "$0" take "$pid" </dev/null >/dev/null 2>&1 & ;;
    isSecure)
      read -r opid conf < <(fakecomp owner "$s")
      [[ $opid == "$pid" && $conf == true ]] && echo true || echo false ;;
  esac
  exit 0
fi
exit 2
EOF

# hyprlock 0.9.5: initializes for $T/hyprlockdelay seconds (if that exists)
# before it asks for the lock, then logs to stdout what CHyprlock::onLockLocked
# and onLockFinished log, as hyprutils prints it. When denied it exits. Mode
# $T/hyprlockmode: normal | broken (exits at once) | limbo (granted, never
# sent `locked`) | linger (denied, but slow to exit).
cat >"$T/bin/hyprlock" <<'EOF'
#!/usr/bin/env bash
R=$POND_TEST_RUN
s=$HYPRLAND_INSTANCE_SIGNATURE
echo "$$" >>"$R/hyprlock.starts"
echo "$*" >"$R/hyprlock.args"
mode=$(cat "$R/hyprlockmode" 2>/dev/null)
[[ $mode != broken ]] || exit 1
echo "Hyprlock version v0.9.5"
sleep "$(cat "$R/hyprlockdelay" 2>/dev/null || echo 0)"
case $(fakecomp request "$s" "$$") in
  granted)
    if [[ $mode != limbo ]]; then
      fakecomp confirm "$s" "$$"
      printf '\033[1;32mDEBUG \033[0m]: onLockLocked called\n'
    fi ;;
  ignored) ;;
  *)
    printf '\033[1;32mDEBUG \033[0m]: onLockFinished called. Seems we got yeeten. Is another lockscreen running?\n'
    [[ $mode != linger ]] || sleep 5
    exit 1 ;;
esac
exec 0<"$R/fifo"
read -r
EOF

# The shell: blocks. $T/shellmode: normal | stubborn (ignores TERM) | fail.
cat >"$T/bin/shell-fake" <<'EOF'
#!/usr/bin/env bash
R=$POND_TEST_RUN
echo "$$ ${POND_SESSION_BIN:-}" >>"$R/shell.starts"
case $(cat "$R/shellmode" 2>/dev/null) in
  stubborn) trap '' TERM ;;
  fail) exit 1 ;;
esac
# $T/shell.run, if present, runs once as a child of this shell (a terminal or
# helper the shell started).
if mv "$R/shell.run" "$R/shell.run.$$" 2>/dev/null; then
  bash "$R/shell.run.$$" &
fi
exec 0<"$R/fifo"
read -r
EOF

# Vicinae: `server` serves until killed, with a helper child like Vicinae's
# file indexer; `ping` answers while a server runs; anything else is recorded.
cat >"$T/bin/vicinae-fake" <<'EOF'
#!/usr/bin/env bash
R=$POND_TEST_RUN
case $1 in
  server)
    echo "$$ $HYPRLAND_INSTANCE_SIGNATURE" >>"$R/vicinae.servers"
    echo "$$ $(start_of $$)" >"$R/vicinae.live"
    bash -c "exec 0<'$R/fifo'; read -r" &
    echo "$!" >"$R/vicinae.helper"
    exec 0<"$R/fifo"
    read -r ;;
  ping)
    read -r p st <"$R/vicinae.live" 2>/dev/null && [[ $(start_of "$p" 2>/dev/null) == "$st" ]] ;;
  *) echo "$HYPRLAND_INSTANCE_SIGNATURE $*" >>"$R/vicinae.calls" ;;
esac
EOF

cat >"$T/bin/systemctl-fake" <<'EOF'
#!/usr/bin/env bash
R=$POND_TEST_RUN
claim=$(cat "$XDG_RUNTIME_DIR/pond-session/claims/notifications" 2>/dev/null)
case "$*" in
  "--user is-active --quiet swaync.service") [[ -e $R/swaync.active ]] ;;
  "--user stop swaync.service") echo "stop swaync claim=$claim" >>"$R/systemctl.calls"; rm -f "$R/swaync.active" ;;
  suspend) echo "suspend locked=$(fakecomp locked "$HYPRLAND_INSTANCE_SIGNATURE")" >>"$R/systemctl.calls" ;;
  *) echo "$*" >>"$R/systemctl.calls" ;;
esac
EOF
cat >"$T/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$POND_TEST_RUN/notify.calls"
EOF
# The real logger would write to the user's journal; gdbus and systemd-inhibit
# must not reach the real system bus. The guard section swaps in the real
# tools against a private bus.
for tool in logger gdbus systemd-inhibit; do
  printf '#!%s\nexit 1\n' "$BASH_BIN" >"$T/bin/$tool"
done
cp "$BASH_BIN" "$T/bin/tmux-like"
chmod +x "$T"/bin/*

{
  printf '#!%s\nset -o nounset -o pipefail\n' "$BASH_BIN"
  cat "$here/pond-session.sh"
} >"$T/bin/pond-session"
chmod +x "$T/bin/pond-session"

export XDG_RUNTIME_DIR=$T/run
export POND_SHELL=$T/bin/shell-fake
export POND_LAUNCHER=$T/bin/vicinae-fake
export POND_LOCK=$T/bin/lock-fake
export POND_SYSTEMCTL=$T/bin/systemctl-fake
export POND_PAM_SERVICE=pond-lock
export PATH=$T/bin:$PATH
# Short bounds so the failure paths finish quickly; the budget check below
# measures against them.
export POND_LOCK_BUDGET=6 POND_SECURE_TIMEOUT=2 POND_IPC_TIMEOUT=0.5
export POND_TERM_GRACE=1
unset POND_SESSION_ID POND_ROLE

SIG=aaaa_1_1   # this session
OTHER=bbbb_2_2 # a second, live Hyprland of the same user
DEAD=cccc_3_3  # a session that is gone

fake_compositor() { # $1 = signature; leaves the pid in $2's variable name
  mkdir -p "$XDG_RUNTIME_DIR/hypr/$1"
  "$T/bin/Hyprland" -c "$BLOCK" &
  printf '%s\nwayland-test\n' "$!" >"$XDG_RUNTIME_DIR/hypr/$1/hyprland.lock"
  printf -v "$2" '%s' "$!"
}
ps_() { HYPRLAND_INSTANCE_SIGNATURE=$SIG pond-session "$@"; }
reg() { # <role> [sig]: the registered pid, if that process still runs
  local pid start
  read -r pid start 2>/dev/null <"$XDG_RUNTIME_DIR/pond-session/${2:-$SIG}/procs/$1" || return 1
  [[ $(start_of "$pid" 2>/dev/null) == "$start" ]] && echo "$pid"
}
alive() { [[ -n ${1:-} && -n $(start_of "$1" 2>/dev/null) ]]; }
owner() { fakecomp owner "${1:-$SIG}" | cut -d' ' -f1; }
comp_locked() { [[ $(fakecomp locked "${1:-$SIG}") == true ]]; }
secure_by() { # <pid>: the compositor is locked, by this live client, confirmed
  local o c
  read -r o c < <(fakecomp owner "$SIG")
  comp_locked && [[ $o == "$1" && $c == true ]] && alive "$1"
}
reset_comp() { rm -rf "$T/comp/$SIG"; }
lines() { [[ -e $1 ]] && wc -l <"$1" || echo 0; }

# Hyprland's first-render start hook can run before createLockFile(). The
# lifecycle helper must not interpret a not-yet-published file as a dead session.
echo "== startup: wait for the compositor before claiming or spawning"
LATE=dddd_4_4
mkdir -p "$XDG_RUNTIME_DIR/hypr/$LATE"
"$T/bin/Hyprland" -c "$BLOCK" & LATE_PID=$!
HYPRLAND_INSTANCE_SIGNATURE=$LATE POND_START_TIMEOUT=3 pond-session start & START_PID=$!
sleep 0.25
check "start waits while the compositor lock file is absent" 'alive "$START_PID"'
check "no shell or notification claim before compositor readiness" '[[ ! -e $T/shell.starts && ! -e $XDG_RUNTIME_DIR/pond-session/claims/notifications ]]'
printf '%s\nwayland-test\n' "$LATE_PID" >"$XDG_RUNTIME_DIR/hypr/$LATE/hyprland.lock"
wait "$START_PID"; rc=$?
wait_for 5 'reg shell "$LATE" >/dev/null && reg launcher "$LATE" >/dev/null'
check "delayed compositor starts both shell and launcher" '[[ $rc -eq 0 ]] && reg shell "$LATE" >/dev/null && reg launcher "$LATE" >/dev/null'
check "supervisor output is retained for startup diagnosis" '[[ -f $XDG_RUNTIME_DIR/pond-session/$LATE/supervise-shell.log && -f $XDG_RUNTIME_DIR/pond-session/$LATE/supervise-launcher.log ]]'
HYPRLAND_INSTANCE_SIGNATURE=$LATE pond-session stop
kill "$LATE_PID"; wait "$LATE_PID" 2>/dev/null
rm -rf "$XDG_RUNTIME_DIR/hypr/$LATE" "$XDG_RUNTIME_DIR/pond-session/$LATE"
rm -f "$T/shell.starts" "$T/vicinae.servers" "$T/vicinae.live" "$T/vicinae.helper" "$T/systemctl.calls" "$T/notify.calls"
HYPRLAND_INSTANCE_SIGNATURE=$LATE POND_START_TIMEOUT=0.3 pond-session start; rc=$?
check "missing compositor fails without starting or claiming anything" '[[ $rc -ne 0 && ! -e $T/shell.starts && ! -e $XDG_RUNTIME_DIR/pond-session/claims/notifications && ! -e $XDG_RUNTIME_DIR/pond-session/claims/launcher ]]'
: >"$T/notify.calls"
HYPRLAND_INSTANCE_SIGNATURE=$LATE pond-session launcher toggle
check "missing launcher does not claim another session owns it" 'grep -q "not running" "$T/notify.calls" && ! grep -q "another Pond session" "$T/notify.calls"'
rm -rf "$XDG_RUNTIME_DIR/pond-session/$LATE"
rm -f "$T/notify.calls"

fake_compositor "$SIG" COMP_SIG

echo "== start: stale sessions are reaped by registration, nothing else is"
mkdir -p "$XDG_RUNTIME_DIR/pond-session/$DEAD/procs"
mkdir -p "$XDG_RUNTIME_DIR/hypr/$DEAD"
printf '999999\nwayland-dead\n' >"$XDG_RUNTIME_DIR/hypr/$DEAD/hyprland.lock"
register_fake() { # <sig> <role> <pid> [start]
  mkdir -p "$XDG_RUNTIME_DIR/pond-session/$1/procs"
  echo "$3 ${4:-$(start_of "$3")}" >"$XDG_RUNTIME_DIR/pond-session/$1/procs/$2"
}
"$T/bin/tmux-like" -c "$BLOCK" & STALE_SHELL=$!
"$T/bin/tmux-like" -c "$BLOCK" & STALE_LOCK=$!
"$T/bin/tmux-like" -c "$BLOCK" & REUSED=$!   # same pid as a record, other process
"$T/bin/tmux-like" -c "$BLOCK" & TAGGED=$!   # carries a stale tag, not registered
register_fake "$DEAD" shell "$STALE_SHELL"
register_fake "$DEAD" lock "$STALE_LOCK"
register_fake "$DEAD" launcher "$REUSED" 1
env POND_SESSION_ID=$DEAD POND_ROLE=launcher "$T/bin/tmux-like" -c "$BLOCK" & TAGGED=$!
fake_compositor "$OTHER" COMP_OTHER
"$T/bin/tmux-like" -c "$BLOCK" & OTHER_SHELL=$!
register_fake "$OTHER" shell "$OTHER_SHELL"
touch "$T/swaync.active"
sleep 0.2

ps_ start
wait_for 5 'reg shell >/dev/null && reg launcher >/dev/null'
check "shell started and registered" 'reg shell >/dev/null'
check "launcher started and registered" 'reg launcher >/dev/null'
check "one supervisor per daemon" 'reg supervise-shell >/dev/null && reg supervise-launcher >/dev/null && reg supervise-guard >/dev/null'
check "shell gets pond-session's own path for its helpers" 'grep -q " $T/bin/pond-session\$" "$T/shell.starts"'
check "registered shell of a dead compositor reaped" '! alive $STALE_SHELL'
check "registered locker of a dead compositor reaped" '! alive $STALE_LOCK'
check "dead session's registry removed" '[[ ! -d $XDG_RUNTIME_DIR/pond-session/$DEAD ]]'
check "reused pid (start time differs) spared" 'alive $REUSED'
check "process with an inherited tag but no registration spared" 'alive $TAGGED'
check "registered process of a live other compositor spared" 'alive $OTHER_SHELL'
check "swaync left running while another session is served by it" '! grep -q swaync "$T/systemctl.calls" 2>/dev/null'
check "no notification claim taken over another session's swaync" '[[ ! -e $XDG_RUNTIME_DIR/pond-session/claims/notifications ]]'

echo "== start is idempotent"
sups=$(reg supervise-shell) shell_pid=$(reg shell)
ps_ start
sleep 0.5
check "same supervisor, same shell" '[[ $(reg supervise-shell) == "$sups" && $(reg shell) == "$shell_pid" ]]'
check "one shell started in total" '[[ $(lines "$T/shell.starts") -eq 1 ]]'

echo "== restart-shell (from a terminal that inherited tags, as a child of the shell)"
echo stubborn >"$T/shellmode"
ps_ restart-shell
launcher_pid=$(reg launcher)
# A child of the (stubborn) shell, standing in for a terminal it started, runs
# restart-shell with the old environment tags.
cat >"$T/shell.run" <<EOF
sleep 0.5
POND_SESSION_ID=$SIG POND_ROLE=shell HYPRLAND_INSTANCE_SIGNATURE=$SIG pond-session restart-shell
echo \$? >"$T/restart.rc"
EOF
ps_ restart-shell
wait_for 6 '[[ -n $(reg shell) && $(reg shell) != "$shell_pid" ]]'
shell_pid=$(reg shell)
check "stubborn (TERM-ignoring) shell is running" 'alive $shell_pid'
wait_for 10 '[[ -e $T/restart.rc ]]'
check "restart-shell from the shell's own tree exits 0 (does not kill itself)" '[[ $(cat "$T/restart.rc" 2>/dev/null) == 0 ]]'
check "TERM-ignoring shell was killed" '! alive $shell_pid'
wait_for 6 '[[ -n $(reg shell) && $(reg shell) != "$shell_pid" ]]'
check "shell has a new pid" '[[ -n $(reg shell) && $(reg shell) != "$shell_pid" ]]'
check "supervisor unchanged" '[[ $(reg supervise-shell) == "$sups" ]]'
check "launcher untouched" '[[ $(reg launcher) == "$launcher_pid" ]]'
echo normal >"$T/shellmode"
ps_ restart-shell
wait_for 6 '[[ -n $(reg shell) ]]'

echo "== a crashed shell is restarted"
shell_pid=$(reg shell)
kill -KILL "$shell_pid"
wait_for 6 '[[ -n $(reg shell) && $(reg shell) != "$shell_pid" ]]'
check "shell came back after SIGKILL" '[[ -n $(reg shell) && $(reg shell) != "$shell_pid" ]]'

echo "== lock: secure means confirmed by the compositor"
t0=$SECONDS
ps_ lock; rc=$?
pond=$(reg lock)
check "fresh lock: exit 0" '[[ $rc -eq 0 ]]'
check "fresh lock: Pond's locker holds a confirmed lock" 'secure_by "$pond"'
check "fresh lock: locker uses the pond-lock PAM service" '[[ $(cat "$T/lock.pam") == pond-lock ]]'
check "fresh lock: IPC addressed to that locker's pid" 'grep -qx "isSecure $pond" "$T/ipc.calls"'
fakecomp unlock "$SIG" "$pond"
ps_ lock; rc=$?
check "running locker: locked over IPC, no second locker" '[[ $rc -eq 0 && $(lines "$T/locker.starts") -eq 1 ]] && secure_by "$pond"'
ps_ lock; rc=$?
check "already secure: exit 0, nothing started" '[[ $rc -eq 0 && $(lines "$T/locker.starts") -eq 1 && ! -e $T/hyprlock.starts ]]'
fakecomp unlock "$SIG" "$pond"

# Between lock cases: no locker left running, an unlocked compositor, defaults.
clean_locks() {
  local p
  for p in $(cat "$T/locker.starts" "$T/hyprlock.starts" 2>/dev/null); do kill -KILL "$p" 2>/dev/null; done
  wait_for 2 '[[ -z $(reg lock)$(reg fallback-lock) ]]'
  reset_comp
  rm -f "$T/lockmode" "$T/hyprlockmode" "$T/lockdelay" "$T/hyprlockdelay" "$T/hyprctl.broken" "$T/hyprctl.hang"
  : >"$T/hyprctl.calls" >"$T/locker.starts"
  rm -f "$T/hyprlock.starts"
}
# Hyprland's restore option was never touched.
no_restore() { ! grep -q '^eval' "$T/hyprctl.calls" 2>/dev/null && [[ ! -e $T/comp/$SIG/restore.log ]]; }
# Not locked with nobody holding the lock (lockdead), which only Hyprland's
# restore option, or a TTY, could recover.
no_lockdead() { ! comp_locked || [[ -n $(owner) ]]; }
# exit 0 only with a live, registered locker of ours holding a confirmed lock.
confirmed_ok() { # <rc>
  [[ $1 -ne 0 ]] || secure_by "$(reg lock)" || secure_by "$(reg fallback-lock)"
}
ms_now() { local t=${EPOCHREALTIME//[!0-9]/}; echo $((10#$t / 1000)); }
secs_ms() { local w=${1%%.*} f; f=${1#"$w"}; f=${f#.}000; echo $((10#${w:-0} * 1000 + 10#${f:0:3})); }

echo "== lock: existing locker accepts the request but never becomes secure"
echo never >"$T/locker.$pond.mode"
t0=$SECONDS
ps_ lock; rc=$?
hl=$(reg fallback-lock)
check "exit 0 only because hyprlock confirmed the lock" '[[ $rc -eq 0 ]] && secure_by "$hl"'
check "hyprlock was started with --immediate-render" '[[ $(cat "$T/hyprlock.args") == --immediate-render ]]'
check "its log is its own: named after its pid and start time" '[[ -e $XDG_RUNTIME_DIR/pond-session/$SIG/hyprlock.$hl.$(start_of "$hl").log ]]'
check "the unconfirmed Pond locker (not the holder) is gone" '! alive $pond'
check "within the budget ($((SECONDS - t0))s)" '((SECONDS - t0 <= POND_LOCK_BUDGET + 1))'
check "the restore option was never touched" 'no_restore'
ps_ lock; rc=$?
check "while hyprlock holds it, lock is a no-op success" '[[ $rc -eq 0 ]] && secure_by "$hl" && [[ $(lines "$T/locker.starts") -eq 1 ]]'
clean_locks

echo "== lock: a new locker that holds the lock but is never confirmed (limbo)"
echo limbo >"$T/lockmode"
: >"$T/notify.calls"
t0=$SECONDS
ps_ lock; rc=$?
pond=$(reg lock)
check "not reported as locked (rc=$rc)" '[[ $rc -eq 1 ]]'
check "still locked by the limbo locker, which was not killed" 'comp_locked && alive "$pond" && [[ $(owner) == "$pond" ]]'
check "hyprlock was denied and is gone" '[[ -z $(reg fallback-lock) ]] && [[ $(lines "$T/hyprlock.starts") -eq 1 ]]'
check "within the budget ($((SECONDS - t0))s)" '((SECONDS - t0 <= POND_LOCK_BUDGET + 1))'
check "the restore option was never touched" 'no_restore'
wait_for 3 'grep -q "Screen not locked" "$T/notify.calls"'
check "the user is told" 'grep -q "Screen not locked" "$T/notify.calls"'
ps_ lock; rc=$?
check "a retry still does not report success (rc=$rc)" '[[ $rc -eq 3 ]] && confirmed_ok $rc && alive "$pond"'
fakecomp confirm "$SIG" "$pond" # Hyprland's 5s `locked` timer
ps_ lock; rc=$?
check "once Hyprland sends it \`locked\`, the same locker is confirmed" '[[ $rc -eq 0 ]] && secure_by "$pond" && [[ $(lines "$T/locker.starts") -eq 1 ]]'
clean_locks

echo "== lock: IPC that hangs is bounded"
echo hang >"$T/lockmode"
t0=$SECONDS
timeout 20 env HYPRLAND_INSTANCE_SIGNATURE=$SIG pond-session lock; rc=$?
pond=$(tail -1 "$T/locker.starts")
check "returned within the budget ($((SECONDS - t0))s)" '((SECONDS - t0 <= POND_LOCK_BUDGET + 1))'
check "not confirmed, so not success" '[[ $rc -ne 0 ]]'
check "the hung locker holds the lock and was not killed" 'comp_locked && alive "$pond" && [[ $(owner) == "$pond" ]]'
check "the restore option was never touched" 'no_restore'
clean_locks

echo "== lock: locker crashes at start"
echo crash >"$T/lockmode"
ps_ lock; rc=$?
hl=$(reg fallback-lock)
check "fell back to hyprlock" '[[ $rc -eq 0 ]] && secure_by "$hl"'
clean_locks

echo "== lock: nothing can lock"
echo never >"$T/lockmode"
echo broken >"$T/hyprlockmode"
: >"$T/notify.calls"
ps_ lock; rc=$?
check "exit non-zero, not locked, no false success" '[[ $rc -eq 1 ]] && ! comp_locked'
wait_for 3 'grep -q "Screen not locked" "$T/notify.calls"'
check "the user is told" 'grep -q "Screen not locked" "$T/notify.calls"'
clean_locks

echo "== lock: Hyprland unreachable"
echo never >"$T/lockmode"
echo broken >"$T/hyprlockmode"
touch "$T/hyprctl.broken"
ps_ lock; rc=$?
pond=$(reg lock)
check "fails, and does not kill Pond's locker blind" '[[ $rc -ne 0 ]] && alive "$pond"'
rm -f "$T/hyprlockmode"
ps_ lock; rc=$?
check "still unreachable: hyprlock confirms on its own, the unconfirmed locker goes" '[[ $rc -eq 0 ]] && secure_by "$(reg fallback-lock)" && ! alive "$pond"'
clean_locks

echo "== lock: Pond's locker died while locked (lockdead) is reported, not taken over"
ps_ lock
pond=$(reg lock)
kill -KILL "$pond"
wait_for 2 '! alive $pond'
check "precondition: compositor still locked, no live locker" 'comp_locked && [[ -z $(owner) ]]'
: >"$T/notify.calls"
ps_ lock; rc=$?
check "exit 3, not success" '[[ $rc -eq 3 ]]'
check "no locker started into a held lock, restore never touched" '[[ $(lines "$T/locker.starts") -eq 1 && ! -e $T/hyprlock.starts ]] && no_restore'
wait_for 3 'grep -q "Lock not confirmed" "$T/notify.calls"'
check "the user is told" 'grep -q "Lock not confirmed" "$T/notify.calls"'
clean_locks

echo "== lock: a lock this script did not start is left alone"
"$T/bin/tmux-like" -c "$BLOCK" & FOREIGN=$!
fakecomp request "$SIG" "$FOREIGN" >/dev/null; fakecomp confirm "$SIG" "$FOREIGN"
ps_ lock; rc=$?
check "exit 3 (locked, not by us), foreign owner unchanged, no restore" '[[ $rc -eq 3 ]] && secure_by "$FOREIGN" && no_restore'
kill "$FOREIGN"; clean_locks

echo "== lock: a record of a locker that unlocked does not authorize anything"
# The final review's stale-ownership case: Pond's locker unlocks and exits,
# its record remains, and another lock screen then holds a confirmed lock.
ps_ lock
pond=$(reg lock)
fakecomp unlock "$SIG" "$pond"
kill -KILL "$pond"
wait_for 2 '! alive $pond'
"$T/bin/tmux-like" -c "$BLOCK" & FOREIGN=$!
fakecomp request "$SIG" "$FOREIGN" >/dev/null; fakecomp confirm "$SIG" "$FOREIGN"
ps_ lock; rc=$?
check "the foreign confirmed owner stays in place" 'secure_by "$FOREIGN"'
check "not reported as our lock (rc=$rc), restore never enabled" '[[ $rc -eq 3 ]] && no_restore'
check "nothing was started into the held lock" '[[ $(lines "$T/locker.starts") -eq 1 && ! -e $T/hyprlock.starts ]]'
kill "$FOREIGN"; clean_locks

echo "== lock: model: why a held lock is never taken over"
# Hyprland 0.56.1: a lock replaced through the restore option is not told, and
# its later unlock_and_destroy unlocks the session under the new lock screen.
"$T/bin/tmux-like" -c "$BLOCK" & OLD=$!
"$T/bin/tmux-like" -c "$BLOCK" & NEW=$!
fakecomp request "$SIG" "$OLD" >/dev/null; fakecomp confirm "$SIG" "$OLD"
fakecomp restore "$SIG" true
r=$(fakecomp request "$SIG" "$NEW"); fakecomp confirm "$SIG" "$NEW"
fakecomp unlock "$SIG" "$OLD"
check "replaced client's unlock clears the lock while the new one is up" '[[ $r == granted ]] && ! comp_locked && [[ $(owner) == "$NEW" ]]'
kill "$OLD" "$NEW"; clean_locks

echo "== lock: lost recovery: a failed attempt is not succeeded by a retry"
echo limbo >"$T/lockmode"
echo broken >"$T/hyprlockmode"
ps_ lock; rc1=$?
echo normal >"$T/lockmode"; rm -f "$T/hyprlockmode"
ps_ lock; rc2=$?
check "first attempt fails (rc=$rc1), retry is not success (rc=$rc2)" '[[ $rc1 -ne 0 && $rc2 -ne 0 ]] && confirmed_ok $rc2'
check "the holder was kept, so it can still be confirmed" 'alive "$(reg lock)" && [[ $(owner) == "$(reg lock)" ]] && no_lockdead'
fakecomp confirm "$SIG" "$(reg lock)"
ps_ lock; rc=$?
check "and is, once it gets \`locked\`" '[[ $rc -eq 0 ]] && secure_by "$(reg lock)"'
check "the restore option was never touched" 'no_restore'
clean_locks

echo "== lock: a failing hyprctl eval is irrelevant: the restore option is never used"
sed -i 's/^  eval)$/  eval) echo error; exit 1 ;;\n  eval-ok)/' "$T/bin/hyprctl"
echo limbo >"$T/lockmode"
ps_ lock; rc=$?
check "limbo lock not reported as success (rc=$rc), nothing touched the option" '[[ $rc -ne 0 ]] && no_restore && no_lockdead'
sed -i 's/^  eval) echo error; exit 1 ;;$//; s/^  eval-ok)$/  eval)/' "$T/bin/hyprctl"
clean_locks

echo "== lock: readiness races (short bounds: Pond ${POND_SECURE_TIMEOUT}s, all ${POND_LOCK_BUDGET}s)"
# race <label> <pond delay> <hyprlock delay> <expected winner: pond|hyprlock|none>
#   [lockmode] [hyprlockmode]: each locker asks for the lock after its delay.
race() {
  local label=$1 pd=$2 hd=$3 want=$4 rc t0 t1 p h
  clean_locks
  echo "$pd" >"$T/lockdelay"; echo "$hd" >"$T/hyprlockdelay"
  [[ -z ${5:-} ]] || echo "$5" >"$T/lockmode"
  [[ -z ${6:-} ]] || echo "$6" >"$T/hyprlockmode"
  : >"$T/locker.starts"; rm -f "$T/hyprlock.starts"
  t0=$(ms_now); ps_ lock 2>/dev/null; rc=$?; t1=$(ms_now)
  p=$(tail -1 "$T/locker.starts" 2>/dev/null) h=$(tail -1 "$T/hyprlock.starts" 2>/dev/null)
  check "$label: within budget ($((t1 - t0))ms)" '((t1 - t0 <= POND_LOCK_BUDGET * 1000 + 500))'
  check "$label: success only with a live confirmed locker of ours (rc=$rc)" 'confirmed_ok $rc'
  case $want in
    pond) check "$label: Pond's locker holds it; hyprlock was dropped" '[[ $rc -eq 0 ]] && secure_by "$p" && ! alive "$h"' ;;
    hyprlock) check "$label: hyprlock holds it; Pond's locker was dropped" '[[ $rc -eq 0 ]] && secure_by "$h" && ! alive "$p"' ;;
    none) check "$label: not success" '[[ $rc -ne 0 ]]' ;;
  esac
  # Let every delayed request land, then: never locked with nobody holding it.
  local wait_ms=$(($(secs_ms "$pd") > $(secs_ms "$hd") ? $(secs_ms "$pd") : $(secs_ms "$hd")))
  wait_ms=$((wait_ms + 500))
  sleep "$((wait_ms / 1000)).$(printf '%03d' $((wait_ms % 1000)))"
  check "$label: afterwards, no lockdead and no restore" 'no_lockdead && no_restore'
}
for i in 1 2 3; do
  race "Pond late, hyprlock still initializing #$i" 2.6 2 pond
  race "hyprlock first, Pond still loading #$i" 3.5 0.4 hyprlock
  race "both late, hyprlock asks first #$i" 3.0 0.3 hyprlock
done
race "Pond in limbo, hyprlock denied" 0 0 none limbo
race "hyprlock denied but slow to exit" 0 0 none limbo linger
race "Pond asks after the budget, hyprlock broken" 7 0 none normal broken

echo "== lock: the final review's reproductions, default bounds"
(
  export POND_LOCK_BUDGET=12 POND_SECURE_TIMEOUT=5 POND_IPC_TIMEOUT=1.5
  race "Pond asks at 5.6s, hyprlock initializes 3s" 5.6 3 pond
  race "Pond asks at 5.6s, hyprlock initializes 0.2s" 5.6 0.2 hyprlock
  printf '%s %s\n' "$pass" "$fail" >"$T/sub.counts"
)
read -r pass fail <"$T/sub.counts"
clean_locks

# A queue of 11.6s ahead of this call leaves 0.4s of the 12s budget.
contended() { # <label>: the caller has set up what hyprctl does
  local t0 t1 rc
  ( flock -x 9; touch "$T/serial-held"; sleep 11.6 ) 9>"$XDG_RUNTIME_DIR/pond-session/$SIG/lock.serial" &
  wait_for 2 '[[ -e $T/serial-held ]]'
  t0=$(ms_now)
  POND_LOCK_BUDGET=12 POND_SECURE_TIMEOUT=5 POND_IPC_TIMEOUT=1.5 ps_ lock 2>/dev/null; rc=$?
  t1=$(ms_now)
  rm -f "$T/serial-held"
  check "$1: within the 12s budget ($((t1 - t0))ms, logind waits 15s)" '((t1 - t0 <= 12500))'
  check "$1: not success (rc=$rc), restore never enabled" '[[ $rc -ne 0 ]] && no_restore'
}
echo "== lock: one deadline for everything, queueing included"
mkdir -p "$XDG_RUNTIME_DIR/pond-session/$SIG/procs" "$T/comp/$SIG"
echo '999999 1' >"$XDG_RUNTIME_DIR/pond-session/$SIG/procs/lock"
echo true >"$T/comp/$SIG/locked"
contended "dead record, session locked"
clean_locks
touch "$T/hyprctl.hang"; echo never >"$T/lockmode"
contended "hyprctl hangs"
clean_locks

echo "== lock: concurrent requests start one locker"
: >"$T/locker.starts"
jobs_=()
for i in 1 2 3; do (ps_ lock; echo $? >>"$T/concurrent.rc") & jobs_+=($!); done
wait "${jobs_[@]}"
check "all succeeded" '[[ $(sort -u "$T/concurrent.rc") == 0 ]]'
check "exactly one locker started" '[[ $(lines "$T/locker.starts") -eq 1 ]] && secure_by "$(reg lock)"'
fakecomp unlock "$SIG" "$(reg lock)"

echo "== a lock started from inside the shell's tree survives restart-shell"
kill -KILL "$(reg lock)"; rm -f "$XDG_RUNTIME_DIR/pond-session/$SIG/procs/lock"; reset_comp
echo slow >"$T/lockmode"
cat >"$T/shell.run" <<EOF
POND_SESSION_ID=$SIG POND_ROLE=shell HYPRLAND_INSTANCE_SIGNATURE=$SIG pond-session lock
echo \$? >"$T/shell-lock.rc"
EOF
rm -f "$T/shell-lock.rc"
ps_ restart-shell                      # the new shell runs shell.run
wait_for 5 '[[ -n $(reg lock) ]]'
ps_ restart-shell                      # while that lock is still waiting
# The `pond-session lock` command itself is part of the shell's tree here and
# may go with it (Pond's shell starts it detached, settings/session.py via
# Quickshell.execDetached); the registered locker it started must not.
locker=$(reg lock)
wait_for 5 'secure_by "$locker"'
check "the locker was not taken for part of the shell, and locks" 'alive "$locker" && secure_by "$locker"'
echo normal >"$T/lockmode"
fakecomp unlock "$SIG" "$(reg lock)"

echo "== suspend locks first and refuses otherwise"
: >"$T/systemctl.calls"
ps_ suspend; rc=$?
check "suspended only after a confirmed lock" '[[ $rc -eq 0 ]] && grep -qx "suspend locked=true" "$T/systemctl.calls"'
fakecomp unlock "$SIG" "$(owner)"
echo never >"$T/lockmode"; echo broken >"$T/hyprlockmode"
kill -KILL "$(reg lock)"; reset_comp; : >"$T/systemctl.calls"
ps_ suspend; rc=$?
check "no lock, no suspend" '[[ $rc -ne 0 ]] && ! grep -q suspend "$T/systemctl.calls"'
echo normal >"$T/lockmode"; rm -f "$T/hyprlockmode"; kill -KILL "$(reg lock)" 2>/dev/null; reset_comp

echo "== launcher: one Vicinae endpoint, owned by one session"
check "this session claimed the launcher" '[[ $(cat "$XDG_RUNTIME_DIR/pond-session/claims/launcher") == "$SIG" ]]'
ps_ launcher toggle; rc=$?
check "launcher command reaches Vicinae for the owner" '[[ $rc -eq 0 ]] && grep -qx "$SIG toggle" "$T/vicinae.calls"'
: >"$T/notify.calls"
HYPRLAND_INSTANCE_SIGNATURE=$OTHER pond-session start
sleep 1.5
check "second Pond session started no second server" '[[ $(grep -c " $OTHER$" "$T/vicinae.servers") -eq 0 ]]'
check "and said so" 'grep -q "Launcher unavailable" "$T/notify.calls"'
HYPRLAND_INSTANCE_SIGNATURE=$OTHER pond-session launcher toggle; rc=$?
check "its launcher binds fail instead of opening the first session's window" '[[ $rc -ne 0 ]] && ! grep -q "^$OTHER " "$T/vicinae.calls"'
check "first session's server untouched, never --replace" '[[ $(reg launcher) == $(head -1 "$T/vicinae.servers" | cut -d" " -f1) ]] && ! grep -rq -- --replace "$T"/vicinae.* '
check "the other session still has its shell" '[[ -n $(reg shell "$OTHER") ]]'
HYPRLAND_INSTANCE_SIGNATURE=$OTHER pond-session stop
check "stopping the other session left this one's launcher" '[[ -n $(reg launcher) ]]'

echo "== notifications: claim, ExecCondition, release"
check "allowed while no Pond session claims them" 'HYPRLAND_INSTANCE_SIGNATURE= pond-session notifications-allowed'
kill "$COMP_OTHER"; wait_for 3 '! alive $COMP_OTHER'
: >"$T/systemctl.calls"; touch "$T/swaync.active"
ps_ stop; ps_ start
wait_for 5 '[[ -n $(reg shell) ]]'
check "claimed before swaync was stopped" 'grep -qx "stop swaync claim=$SIG" "$T/systemctl.calls"'
check "swaync may not start while Pond owns notifications" '! pond-session notifications-allowed'
fake_compositor "$OTHER" COMP_OTHER
ps_ stop
check "released on stop" 'pond-session notifications-allowed && [[ ! -e $XDG_RUNTIME_DIR/pond-session/claims/launcher ]]'
check "launcher released on stop" '[[ ! -e $XDG_RUNTIME_DIR/pond-session/claims/launcher ]]'
check "other live session, swaync inactive: claim taken" 'rm -f "$T/swaync.active"; ps_ start; [[ $(cat "$XDG_RUNTIME_DIR/pond-session/claims/notifications") == "$SIG" ]]'
ps_ stop

echo "== stop"
ps_ start
wait_for 5 '[[ -n $(reg shell) && -n $(reg launcher) ]]'
ps_ lock
helper=$(cat "$T/vicinae.helper")
"$T/bin/tmux-like" -c "$BLOCK" & APP=$!  # an app Vicinae launched (detached)
env POND_SESSION_ID=$SIG POND_ROLE=launcher HYPRLAND_INSTANCE_SIGNATURE=$SIG pond-session stop; rc=$?
check "stop from a tagged environment exits 0" '[[ $rc -eq 0 ]]'
check "supervisors, shell, launcher, guard, locker gone" '[[ -z $(reg supervise-shell)$(reg supervise-launcher)$(reg supervise-guard)$(reg shell)$(reg launcher)$(reg guard)$(reg lock) ]]'
check "Vicinae's helper child stopped with it" '! alive $helper'
check "nothing respawned" '! wait_for 2 "[[ -n \$(reg shell) ]]"'
check "detached app spared" 'alive $APP'
check "live other compositor's process spared" 'alive $OTHER_SHELL'
reset_comp

echo "== compositor dies while children live (no shutdown hook)"
ps_ start
wait_for 5 '[[ -n $(reg shell) && -n $(reg launcher) && -n $(reg guard) ]]'
ps_ lock
shell_pid=$(reg shell) launcher_pid=$(reg launcher) guard_pid=$(reg guard) pond=$(reg lock)
helper=$(cat "$T/vicinae.helper")
kill "$COMP_SIG"
wait_for 6 '! alive $shell_pid && ! alive $launcher_pid && [[ -z $(reg supervise-shell)$(reg supervise-launcher)$(reg supervise-guard) ]]'
check "shell stopped" '! alive $shell_pid'
check "launcher and its helper stopped" '! alive $launcher_pid && ! alive $helper'
check "guard stopped" '! alive $guard_pid'
check "locker stopped" '! alive $pond'
check "supervisors exited" '[[ -z $(reg supervise-shell)$(reg supervise-launcher)$(reg supervise-guard) ]]'
check "claims released" '[[ ! -e $XDG_RUNTIME_DIR/pond-session/claims/launcher ]] && pond-session notifications-allowed'
check "other session untouched" 'alive $OTHER_SHELL'

echo "== a shell that cannot start does not spin"
SIG=dddd_4_4 # a new compositor has a new signature
fake_compositor "$SIG" COMP_SIG
echo fail >"$T/shellmode"; : >"$T/shell.starts"
ps_ start
wait_for 12 '[[ -z $(reg supervise-shell) ]]'
check "gave up after five quick failures (starts=$(lines "$T/shell.starts"))" '[[ $(lines "$T/shell.starts") -eq 5 && -z $(reg supervise-shell) ]]'
check "launcher supervisor unaffected" '[[ -n $(reg supervise-launcher) ]]'
echo normal >"$T/shellmode"
ps_ restart-shell
wait_for 5 '[[ -n $(reg shell) ]]'
check "restart-shell brings a given-up shell back" '[[ -n $(reg shell) ]]'
ps_ stop

echo "== lock guard: logind on a private D-Bus"
if [[ -n ${POND_TEST_PYGI:-} ]] && command -v dbus-daemon gdbus systemd-inhibit >/dev/null; then
  # Real gdbus and systemd-inhibit; the guard runs under its supervisor.
  rm -f "$T/bin/gdbus" "$T/bin/systemd-inhibit"
  read -r DBUS_SYSTEM_BUS_ADDRESS DBUS_PID < <(dbus-daemon --session --fork --print-address=1 --print-pid=1 | tr '\n' ' ')
  export DBUS_SYSTEM_BUS_ADDRESS
  mkfifo "$T/login1.ctl"
  ev=$T/login1.events
  "$POND_TEST_PYGI" -I -W ignore "$here/fake-login1.py" "$T/login1.ctl" "$ev" /org/freedesktop/login1/session/_32 &
  wait_for 5 'grep -qx owned "$ev" 2>/dev/null'
  export XDG_SESSION_ID=2
  reset_comp; echo normal >"$T/lockmode"
  ps_ start
  wait_for 5 'grep -q "^inhibit sleep delay" "$ev"'
  check "guard holds a sleep delay inhibitor" 'grep -qx "inhibit sleep delay" "$ev"'
  echo "sleep true" >"$T/login1.ctl"
  wait_for 10 'grep -qx released "$ev"'
  check "PrepareForSleep: locked, then the inhibitor released" 'grep -qx released "$ev" && secure_by "$(reg lock)"'
  echo "sleep false" >"$T/login1.ctl"
  wait_for 5 '[[ $(grep -c "^inhibit" "$ev") -eq 2 ]]'
  check "resume: inhibitor taken again" '[[ $(grep -c "^inhibit" "$ev") -eq 2 ]]'
  fakecomp unlock "$SIG" "$(reg lock)"
  echo lock-other >"$T/login1.ctl"; sleep 1
  check "another session's Lock signal is ignored" '! comp_locked'
  echo lock >"$T/login1.ctl"
  wait_for 6 'secure_by "$(reg lock)"'
  check "this session's Lock signal locks" 'secure_by "$(reg lock)"'
  fakecomp unlock "$SIG" "$(reg lock)"

  # Nothing can lock before sleep: logind is not held past the budget, the
  # failure is reported, and the lock is retried on resume.
  echo never >"$T/lockmode"; echo broken >"$T/hyprlockmode"
  kill -KILL "$(reg lock)"; reset_comp; : >"$T/notify.calls"
  t0=$SECONDS
  echo "sleep true" >"$T/login1.ctl"
  wait_for 12 '[[ $(grep -c "^released" "$ev") -eq 2 ]]'
  check "inhibitor released within the budget even when locking failed ($((SECONDS - t0))s)" '[[ $(grep -c "^released" "$ev") -eq 2 ]] && ((SECONDS - t0 <= POND_LOCK_BUDGET + 2))'
  check "not reported as locked" '! comp_locked'
  echo normal >"$T/lockmode"; rm -f "$T/hyprlockmode"
  kill -KILL "$(reg lock)" 2>/dev/null
  echo "sleep false" >"$T/login1.ctl"
  wait_for 10 'secure_by "$(reg lock)"'
  check "resume after a failed lock locks at once" 'secure_by "$(reg lock)"'
  fakecomp unlock "$SIG" "$(reg lock)"

  guard_pid=$(reg guard)
  kill -KILL "$guard_pid"
  wait_for 5 '[[ $(grep -c "^inhibit" "$ev") -eq 4 ]]'
  check "a killed guard is restarted and its inhibitor dropped and retaken" '[[ $(grep -c "^released" "$ev") -ge 3 && $(grep -c "^inhibit" "$ev") -eq 4 && -n $(reg guard) && $(reg guard) != "$guard_pid" ]]'
  ps_ stop
  wait_for 3 '[[ $(grep -c "^released" "$ev") -ge 4 ]]'
  check "stop releases the inhibitor" '[[ $(grep -c "^released" "$ev") -ge 4 ]]'
else
  skipped=$((skipped + 1))
  printf '  SKIPPED lock guard (needs POND_TEST_PYGI, dbus-daemon, gdbus, systemd-inhibit)\n'
fi

echo
printf '%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skipped"
[[ $fail -eq 0 && $skipped -eq 0 ]]
