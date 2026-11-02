# Upstream pin. To update: change rev, set hash = lib.fakeHash, build, paste
# the reported hash, then re-run the checks listed in ./README.md.
{ fetchFromGitHub }:
fetchFromGitHub {
  owner = "bflycomputer";
  repo = "pond-interface";
  rev = "f77883588cf0b27bc4c5fa5c0ee2dc1814a448a6";
  hash = "sha256-2EgE709Civ4NvFekGcfYvHPIDWr8TH/areTKhZktCZo=";
}
