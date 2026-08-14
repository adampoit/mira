{
  buildNpmPackage,
  lib,
  makeWrapper,
  nodejs,
  src,
}:
buildNpmPackage {
  pname = "mira-pi-worker";
  version = "0.1.0";
  inherit src;

  npmDepsHash = "sha256-GVT8bFRZs5h6PxukJw8Zm3Ae5mnYS18V4Ty5PhAzLlI=";
  npmDepsFetcherVersion = 2;
  dontNpmBuild = true;
  nativeBuildInputs = [makeWrapper];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib/mira-pi-worker $out/bin
    cp -r worker.js package.json node_modules $out/lib/mira-pi-worker/
    makeWrapper ${lib.getExe nodejs} $out/bin/mira-pi-worker \
      --add-flags $out/lib/mira-pi-worker/worker.js
    runHook postInstall
  '';

  meta = {
    description = "Pi agent worker for Mira code reviews";
    mainProgram = "mira-pi-worker";
  };
}
