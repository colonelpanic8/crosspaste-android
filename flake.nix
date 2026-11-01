{
  description = "CrossPaste Community Android APK patches and release tooling";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
          android_sdk.accept_license = true;
        };
      };
      androidComposition = pkgs.androidenv.composeAndroidPackages {
        cmdLineToolsVersion = "22.0";
        toolsVersion = "26.1.1";
        platformToolsVersion = "37.0.1";
        buildToolsVersions = ["37.0.0"];
        platformVersions = ["37"];
        includeEmulator = false;
        includeSources = false;
        includeSystemImages = false;
        includeNDK = false;
        useGoogleAPIs = false;
        useGoogleTVAddOns = false;
      };
      androidHome = "${androidComposition.androidsdk}/libexec/android-sdk";
      apkeditor = pkgs.fetchurl {
        url = "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar";
        hash = "sha256-qc1A34GIRUVr5taW3mEQyJ7fSwoFgMuDQ47Wslo2bmc=";
      };
      python = pkgs.python3.withPackages (ps: with ps; [androguard loguru pyyaml]);
      tools = with pkgs; [
        alejandra
        apktool
        curl
        fdroidserver
        gh
        git
        jdk17
        just
        python
        ruff
        shellcheck
      ];
      builder = pkgs.writeShellApplication {
        name = "crosspaste-build";
        runtimeInputs = tools;
        text = ''
          export ANDROID_HOME=${androidHome}
          export APKEDITOR_JAR=${apkeditor}
          export PATH=${androidHome}/build-tools/37.0.0:${androidHome}/platform-tools:$PATH
          exec python3 ${self}/scripts/build.py "$@"
        '';
      };
      androidShell = pkgs.mkShell {
        packages = tools ++ [androidComposition.androidsdk];
        ANDROID_HOME = androidHome;
        ANDROID_SDK_ROOT = androidHome;
        JAVA_HOME = pkgs.jdk17.home;
        APKEDITOR_JAR = apkeditor;
        shellHook = ''
          export PATH=${androidHome}/build-tools/37.0.0:${androidHome}/platform-tools:$PATH
        '';
      };
    in {
      packages.default = builder;
      apps.default = {
        type = "app";
        program = "${builder}/bin/crosspaste-build";
      };
      devShells = {
        default = androidShell;
        android = androidShell;
      };
      formatter = pkgs.alejandra;
    });
}
