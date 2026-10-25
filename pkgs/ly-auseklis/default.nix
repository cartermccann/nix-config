{
  lib,
  runCommand,
  python3,
  ly,
}:
# Ly greeter assets in Portfolio2's language (see modules/desktop-wayland.nix):
#
#   auseklis.dur  the CM/26 monogram resolving coarse -> sharp (make-dur.py)
#   auseklis.ini  a language file: Ly's English strings with slash-pair labels
#                ("01 / USER"); every key is kept so Ly's parser finds them all
let
  labels = {
    login = "01 / USER";
    password = "02 / PASS";
    shutdown = "/ SHUTDOWN";
    restart = "/ REBOOT";
    sleep = "/ SLEEP";
    brightness_down = "/ BRIGHT -";
    brightness_up = "/ BRIGHT +";
    toggle_password = "/ SHOW PASS";
    authenticating = "AUTH / ...";
    logout = "SESSION / ENDED";
  };
  lines = lib.mapAttrsToList (key: label: "${key} = ${label}") labels;
in
runCommand "ly-auseklis" { nativeBuildInputs = [ python3 ]; } ''
  mkdir -p $out
  cp ${./monogram.py} monogram.py
  PYTHONPATH=. python3 ${./make-dur.py} $out/auseklis.dur

  sed ${
    lib.concatStringsSep " " (
      lib.mapAttrsToList (
        key: label: "-e " + lib.escapeShellArg "s|^${key} = .*|${key} = ${label}|"
      ) labels
    )
  } ${ly.src}/res/lang/en.ini > $out/auseklis.ini

  # sed exits 0 whether or not a key matched; fail the build if upstream
  # renamed one instead of shipping a half-English greeter.
  for want in ${lib.escapeShellArgs lines}; do
    grep -qxF "$want" $out/auseklis.ini || { echo "ly lang: '$want' did not apply" >&2; exit 1; }
  done
''
