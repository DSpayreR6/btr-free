{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = [
    (pkgs.python312.withPackages (ps: [
      ps.textual
    ]))
  ];

  shellHook = ''
    echo "btr-free dev shell"
    echo "Start: sudo python -m btr_free"
  '';
}
