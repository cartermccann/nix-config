{ upstream }:
upstream.overridePythonAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./contract-8.patch ];
})
