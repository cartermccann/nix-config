{ upstream }:
upstream.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./t3-0.1.5-compatibility.patch ];
})
