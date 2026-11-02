# Fonts the Pond QML asks for by family name, plus the fontconfig file the
# wrappers point at. Families referenced upstream (rev in ./source.nix):
#   Onest, Pond Gramercy, IBM Plex Mono, Advent Pro, JetBrains Mono,
#   and fastfetch-flowers (the glyph font of Pond's fastfetch config).
{
  lib,
  stdenvNoCC,
  fetchurl,
  writeText,
  ibm-plex,
  jetbrains-mono,
  src,
  # A package providing the proprietary "Pond Gramercy" family under
  # share/fonts. Upstream does not distribute it (iso-installer README: "Pond
  # Gramercy is proprietary and is not included in this repository"), and no
  # public Pond package ships it, so it defaults to absent.
  gramercyFont ? null,
  # Family fontconfig substitutes when "Pond Gramercy" is not installed.
  gramercyFallbackFamily ? "Onest",
}:
let
  # google/fonts, pinned to the revision nixpkgs' google-fonts uses, so only
  # the three files Pond needs are downloaded instead of the whole repository.
  googleFontsRev = "5174b3333331c966c38f4355d50b03ca1c1df2f9";
  googleFont =
    {
      name,
      path,
      hash,
    }:
    fetchurl {
      inherit name hash;
      url = "https://raw.githubusercontent.com/google/fonts/${googleFontsRev}/${path}";
    };

  onest = googleFont {
    name = "Onest-wght.ttf";
    path = "ofl/onest/Onest%5Bwght%5D.ttf";
    hash = "sha256-P6pLkFZhhJsjMuOUtC+Rtb9VdeVTxRbKqBgR6Gik1Yk=";
  };
  onestLicense = googleFont {
    name = "OFL-Onest.txt";
    path = "ofl/onest/OFL.txt";
    hash = "sha256-BxGV2IBuIm+u7mAlnCjKZ7RYInr1GVpz9c/KsG4wA7w=";
  };
  adventPro = googleFont {
    name = "AdventPro-wdth-wght.ttf";
    path = "ofl/adventpro/AdventPro%5Bwdth%2Cwght%5D.ttf";
    hash = "sha256-tgqykguJvcS32t+ygsraxuA4M+eoCET56sCWNZsrl2Y=";
  };
  adventProItalic = googleFont {
    name = "AdventPro-Italic-wdth-wght.ttf";
    path = "ofl/adventpro/AdventPro-Italic%5Bwdth%2Cwght%5D.ttf";
    hash = "sha256-CRel1lWJAl8qdSsbBWj5JNrEmgZaJbTWARazxtTZ664=";
  };
  adventProLicense = googleFont {
    name = "OFL-AdventPro.txt";
    path = "ofl/adventpro/OFL.txt";
    hash = "sha256-yNUramwqbEYZOkjU5txzaIlrx9TAafM6vKByhHaVeuw=";
  };

  pondFonts = stdenvNoCC.mkDerivation {
    pname = "pond-interface-fonts";
    version = "0-unstable";
    dontUnpack = true;
    installPhase = ''
      runHook preInstall
      dir=$out/share/fonts/truetype/pond-interface
      install -Dm644 ${onest} $dir/Onest-wght.ttf
      install -Dm644 ${adventPro} $dir/AdventPro-wdth-wght.ttf
      install -Dm644 ${adventProItalic} $dir/AdventPro-Italic-wdth-wght.ttf
      install -Dm644 ${src}/config/fastfetch/fastfetch-flowers.ttf $dir/fastfetch-flowers.ttf
      install -Dm644 ${onestLicense} $out/share/licenses/pond-interface-fonts/OFL-Onest.txt
      install -Dm644 ${adventProLicense} $out/share/licenses/pond-interface-fonts/OFL-AdventPro.txt
      install -Dm644 ${src}/LICENSE $out/share/licenses/pond-interface-fonts/LICENSE-fastfetch-flowers
      runHook postInstall
    '';
    meta = {
      description = "Fonts used by the Pond interface (Onest, Advent Pro, fastfetch-flowers)";
      # Onest and Advent Pro are OFL-1.1; fastfetch-flowers ships in the
      # BSD-3-Clause pond-interface repository.
      license = with lib.licenses; [
        ofl
        bsd3
      ];
      platforms = lib.platforms.all;
    };
  };

  packages = [
    pondFonts
    (ibm-plex.override { families = [ "mono" ]; })
    jetbrains-mono
  ]
  ++ lib.optional (gramercyFont != null) gramercyFont;

  # Wrapper-only fontconfig: Pond's families first, then the host's normal
  # configuration (which still pulls in user fontconfig). The Gramercy alias
  # uses <accept>, so a real "Pond Gramercy" anywhere on the system still wins
  # and the fallback is only inserted directly after it, ahead of the generic
  # sans-serif defaults.
  fontsConf = writeText "pond-interface-fonts.conf" ''
    <?xml version="1.0"?>
    <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
    <fontconfig>
    ${lib.concatMapStrings (p: "  <dir>${p}/share/fonts</dir>\n") packages}
      <alias binding="same">
        <family>Pond Gramercy</family>
        <accept><family>${gramercyFallbackFamily}</family></accept>
      </alias>
      <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
      <cachedir prefix="xdg">fontconfig</cachedir>
    </fontconfig>
  '';
in
{
  inherit packages fontsConf pondFonts;
  gramercyAvailable = gramercyFont != null;
}
