{
  lib,
  stdenvNoCC,
  bash,
  fetchFromGitHub,
  python3,
  libsecret,
  nix-update-script,
}:

let
  python = python3.withPackages (ps: [ ps.zstandard ]);
in
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "tidesi-cli";
  version = "3.3.1";

  src = fetchFromGitHub {
    owner = "Muddyblack";
    repo = "tidesi";
    tag = "v${finalAttrs.version}";
    hash = "sha256-RT/x9eEBrnbOpa6ghyDrdntsXrKl4fslxGJahn82CrM=";
  };

  # strictDeps: patchShebangs only rewrites `#!/usr/bin/env bash` to an
  # interpreter that is a runtime input.
  buildInputs = [ bash ];
  strictDeps = true;
  __structuredAttrs = true;
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    dest=$out/share/tidesi
    mkdir -p "$dest" "$out/bin"
    cp -r backend "$dest/backend"
    find "$dest" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +

    substituteInPlace "$dest/backend/sh/python-interp.sh" \
      --replace-fail 'PY_DEFAULT="python3"' \
                     'PY_DEFAULT="${lib.getExe python}"'
    substituteInPlace "$dest/backend/tidesi/providers/antigravity_remote.py" \
      --replace-fail 'SECRET_TOOL = "secret-tool"' \
                     'SECRET_TOOL = "${lib.getExe' libsecret "secret-tool"}'
    substituteInPlace "$dest/backend/tidesi/providers/zed.py" \
      --replace-quiet 'SECRET_TOOL = "secret-tool"' \
                      'SECRET_TOOL = "${lib.getExe' libsecret "secret-tool"}'

    # The launchers resolve symlinks to find the tidesi package beside them.
    ln -s "$dest/backend/sh/tidesi-cli" "$out/bin/tidesi-cli"
    ln -s "$dest/backend/sh/get-ai-usage" "$out/bin/get-ai-usage"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    $out/bin/tidesi-cli --help > /dev/null
    runHook postInstallCheck
  '';

  passthru.updateScript = nix-update-script { };

  meta = {
    description = "Terminal frontend of Tidesi: provider limits as a table or a status-bar line";
    homepage = "https://github.com/Muddyblack/tidesi";
    changelog = "https://github.com/Muddyblack/tidesi/releases/tag/${finalAttrs.src.tag}";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "tidesi-cli";
    maintainers = [ ];
  };
})
