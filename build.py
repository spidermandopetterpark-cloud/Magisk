#!/usr/bin/env python3

import argparse
import functools
import glob
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from zipfile import ZipFile


sys.dont_write_bytecode = True

from scripts.env import *


# ============================================================
# CONSTANTS
# ============================================================

SUPPORT_ABIS = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "thumbv7neon-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
    "riscv64": "riscv64-linux-android",
}

ABI_ALIAS = {
    "arm": "armeabi-v7a",
    "arm32": "armeabi-v7a",
    "arm64": "arm64-v8a",
    "aarch64": "arm64-v8a",
    "x64": "x86_64",
    "amd64": "x86_64",
}

DEFAULT_ABIS = set(SUPPORT_ABIS) - {"riscv64"}

SUPPORT_TARGETS = {
    "magisk",
    "magiskinit",
    "magiskboot",
    "magiskpolicy",
    "resetprop",
}

DEFAULT_TARGETS = SUPPORT_TARGETS - {"resetprop"}

RUST_TARGETS = set(DEFAULT_TARGETS)

CLEAN_TARGETS = {
    "native",
    "cpp",
    "rust",
    "app",
}


# ============================================================
# GLOBALS
# ============================================================

config = {}

args = None

build_abis = {}

force_out = False


# ============================================================
# ERROR / OUTPUT
# ============================================================

def fail(message):
    raise RuntimeError(str(message))


def vprint(message):
    if args is not None and args.verbose > 0:
        print(message)


# ============================================================
# FILE HELPERS
# ============================================================

def mv(source: Path, target: Path):
    source = Path(source)
    target = Path(target)

    if not source.exists():
        vprint(f"skip mv: {source}")
        return False

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if target.exists():
        rm_rf(target)

    shutil.move(
        str(source),
        str(target),
    )

    vprint(f"mv {source} -> {target}")

    return True


def cp(source: Path, target: Path):
    source = Path(source)
    target = Path(target)

    if not source.exists():
        vprint(f"skip cp: {source}")
        return False

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copyfile(
        str(source),
        str(target),
    )

    vprint(f"cp {source} -> {target}")

    return True


def rm(file: Path):
    file = Path(file)

    try:
        file.unlink()
        vprint(f"rm {file}")
    except FileNotFoundError:
        pass


def rm_on_error(func, path, exc_info):
    try:
        os.chmod(
            path,
            stat.S_IWRITE,
        )
        func(path)
    except FileNotFoundError:
        pass


def rm_rf(path: Path):
    path = Path(path)

    if not path.exists():
        return

    vprint(f"rm -rf {path}")

    if sys.version_info >= (3, 12):
        shutil.rmtree(
            path,
            onexc=rm_on_error,
        )
    else:
        shutil.rmtree(
            path,
            onerror=rm_on_error,
        )


# ============================================================
# PROCESS HELPERS
# ============================================================

def execv(cmds):
    """
    Execute a command.

    On Unix, commands are passed normally.
    On Windows, shell=True is used only when required by
    scripts supplied by the project.
    """

    if isinstance(cmds, str):
        command = cmds
        use_shell = True
    else:
        command = [
            str(x)
            for x in cmds
        ]
        use_shell = bool(is_windows)

    stdout = None

    if (
        not force_out
        and args is not None
        and args.verbose == 0
    ):
        stdout = subprocess.DEVNULL

    vprint("$ " + (
        command
        if isinstance(command, str)
        else " ".join(command)
    ))

    return subprocess.run(
        command,
        stdout=stdout,
        shell=use_shell,
        check=False,
    )


def cmd_out(cmds):
    if isinstance(cmds, str):
        command = cmds
        use_shell = True
    else:
        command = [
            str(x)
            for x in cmds
        ]
        use_shell = bool(is_windows)

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=use_shell,
            check=False,
        )

        return result.stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()

    except OSError:
        return ""


def command_exists(command):
    return shutil.which(str(command)) is not None


# ============================================================
# NDK
# ============================================================

def setup_ndk():

    ndk = Path(paths().ndk)

    ndk_parent = ndk.parent

    ndk_parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    url = (
        "https://github.com/topjohnwu/ondk/releases/download/"
        f"{ondk_version}/"
        f"ondk-{ondk_version}-{os_name}.tar.xz"
    )

    archive_name = url.rsplit("/", 1)[-1]

    extracted = (
        ndk_parent
        / f"ondk-{ondk_version}"
    )

    header(
        f"* Downloading {archive_name}"
    )

    rm_rf(extracted)

    try:

        with urllib.request.urlopen(
            url,
            timeout=60,
        ) as response:

            with tarfile.open(
                mode="r|xz",
                fileobj=response,
            ) as archive:

                if hasattr(
                    tarfile,
                    "data_filter",
                ):
                    archive.extractall(
                        ndk_parent,
                        filter="tar",
                    )
                else:
                    archive.extractall(
                        ndk_parent,
                    )

    except Exception as exc:

        error(
            "Failed to download NDK: "
            f"{exc}"
        )
        return

    rm_rf(ndk)

    if not extracted.exists():

        error(
            "NDK archive was extracted, "
            "but the expected directory was not found: "
            f"{extracted}"
        )

        return

    mv(
        extracted,
        ndk,
    )

    if not ndk.exists():

        error(
            "NDK installation failed. "
            f"Expected: {ndk}"
        )

        return

    header(
        "* NDK installed successfully"
    )


def ensure_project_ndk():

    ndk = Path(paths().ndk)

    if not ndk.exists():

        header(
            "! Project NDK not found"
        )

        header(
            "! Installing project NDK..."
        )

        setup_ndk()

    if not ndk.exists():

        error(
            "Project NDK is still missing: "
            f"{ndk}"
        )


# ============================================================
# ELF CLEANER
# ============================================================

def clean_elf():

    ensure_cargo()

    cargo_toml = Path(
        "tools",
        "elf-cleaner",
        "Cargo.toml",
    )

    if not cargo_toml.exists():

        error(
            "ELF cleaner Cargo.toml not found: "
            f"{cargo_toml}"
        )

        return

    files = []

    files.extend(
        glob.glob(
            "native/out/*/magisk"
        )
    )

    files.extend(
        glob.glob(
            "native/out/*/magiskpolicy"
        )
    )

    if not files:
        return

    commands = [
        "cargo",
        "run",
        "--release",
        "--manifest-path",
        str(cargo_toml),
    ]

    if args.verbose == 0:
        commands.append("-q")
    elif args.verbose > 1:
        commands.append("--verbose")

    commands.append("--")

    commands.extend(files)

    result = execv(commands)

    if result.returncode != 0:

        error(
            "ELF cleaner failed!"
        )


# ============================================================
# NDK BUILD
# ============================================================

def collect_ndk_build():

    for abi in build_abis:

        source_dir = Path(
            "native",
            "libs",
            abi,
        )

        output_dir = Path(
            "native",
            "out",
            abi,
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not source_dir.exists():
            continue

        for source in source_dir.iterdir():

            target = (
                output_dir
                / source.name
            )

            mv(
                source,
                target,
            )


def run_ndk_build(commands):

    ensure_project_ndk()

    old_dir = Path.cwd()

    try:

        os.chdir("native")

        cmd = list(commands)

        cmd.extend(
            [
                "NDK_PROJECT_PATH=.",
                "NDK_APPLICATION_MK=src/Application.mk",
                "APP_ABI=" + " ".join(build_abis.keys()),
                f"-j{cpu_count}",
            ]
        )

        if args.verbose > 1:
            cmd.append("V=1")

        if not args.release:
            cmd.append(
                "MAGISK_DEBUG=1"
            )

        ndk_build = Path(
            paths().ndk_build
        )

        if not ndk_build.exists():

            error(
                "ndk-build not found: "
                f"{ndk_build}"
            )

            return

        result = execv(
            [
                ndk_build,
                *cmd,
            ]
        )

        if result.returncode != 0:

            error(
                "Native NDK build failed!"
            )

    finally:
        os.chdir(old_dir)


# ============================================================
# C++
# ============================================================

def build_cpp_src(targets):

    commands = []

    clean = False

    if "magisk" in targets:

        commands.append(
            "B_MAGISK=1"
        )

        clean = True

    if "magiskpolicy" in targets:

        commands.append(
            "B_POLICY=1"
        )

        clean = True

    if "magiskinit" in targets:

        commands.append(
            "B_PRELOAD=1"
        )

    if "resetprop" in targets:

        commands.append(
            "B_PROP=1"
        )

    if commands:

        run_ndk_build(commands)

        collect_ndk_build()

    commands = []

    if "magiskinit" in targets:

        commands.append(
            "B_INIT=1"
        )

    if "magiskboot" in targets:

        commands.append(
            "B_BOOT=1"
        )

    if commands:

        commands.append(
            "B_CRT0=1"
        )

        run_ndk_build(commands)

        collect_ndk_build()

    if clean:
        clean_elf()


# ============================================================
# RUST
# ============================================================

def build_rust_src(targets):

    ensure_cargo()

    targets = set(targets)

    if "resetprop" in targets:
        targets.add("magisk")

    targets &= RUST_TARGETS

    if not targets:
        return

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        if args.release:
            profile = "release"
        else:
            profile = "debug"

        base = [
            "cargo",
            "build",
            "-p",
        ]

        if args.release:
            base.append(
                "--release"
            )

        if args.verbose == 0:
            base.append("-q")
        elif args.verbose > 1:
            base.append(
                "--verbose"
            )

        for triple in build_abis.values():

            base.extend(
                [
                    "--target",
                    triple,
                ]
            )

        for target in sorted(targets):

            commands = list(base)

            commands.insert(
                3,
                target,
            )

            result = execv(commands)

            if result.returncode != 0:

                error(
                    "Rust build failed: "
                    f"{target}"
                )

    finally:

        os.chdir(old_dir)

    native_out = Path(
        "native",
        "out",
    )

    rust_out = (
        native_out
        / "rust"
    )

    for abi, triple in build_abis.items():

        abi_out = (
            native_out
            / abi
        )

        abi_out.mkdir(
            mode=0o755,
            parents=True,
            exist_ok=True,
        )

        for target in sorted(targets):

            source = (
                rust_out
                / triple
                / profile
                / f"lib{target}.a"
            )

            destination = (
                abi_out
                / f"lib{target}-rs.a"
            )

            mv(
                source,
                destination,
            )


# ============================================================
# GENERATED FLAGS
# ============================================================

def write_if_diff(
    filename: Path,
    text: str,
):

    filename = Path(filename)

    if filename.exists():

        try:

            current = filename.read_text(
                encoding="utf-8"
            )

            if current == text:
                return

        except OSError:
            pass

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename.write_text(
        text,
        encoding="utf-8",
    )


def dump_flags_native():

    version = config["version"]
    version_code = config["versionCode"]

    text = (
        "#pragma once\n"
        f'#define MAGISK_VERSION "{version}"\n'
        f"#define MAGISK_VER_CODE {version_code}\n"
        f"#define MAGISK_DEBUG "
        f"{0 if args.release else 1}\n"
    )

    generated = Path(
        "native",
        "out",
        "generated",
    )

    generated.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_if_diff(
        generated / "flags.h",
        text,
    )

    rust_text = (
        f'pub const MAGISK_VERSION: &str = "{version}";\n'
        f"pub const MAGISK_VER_CODE: i32 = {version_code};\n"
    )

    write_if_diff(
        generated / "flags.rs",
        rust_text,
    )


# ============================================================
# NATIVE BUILD
# ============================================================

def build_native():

    ensure_project_ndk()
    ensure_toolchain()

    requested = getattr(
        args,
        "targets",
        None,
    )

    if requested:

        targets = (
            set(requested)
            & SUPPORT_TARGETS
        )

        invalid = (
            set(requested)
            - SUPPORT_TARGETS
        )

        if invalid:

            error(
                "Unknown native target(s): "
                + ", ".join(
                    sorted(invalid)
                )
            )

    else:

        targets = set(
            DEFAULT_TARGETS
        )

    if not targets:
        return

    header(
        "* Building native: "
        + " ".join(
            sorted(targets)
        )
    )

    dump_flags_native()

    build_rust_src(targets)

    build_cpp_src(targets)


# ============================================================
# APP FLAGS
# ============================================================

def dump_flags_app():

    text = (
        "abiList="
        + ",".join(build_abis.keys())
        + "\n"
    )

    text += (
        f"version={config['version']}\n"
    )

    text += (
        f"versionCode={config['versionCode']}\n"
    )

    output = Path(
        "app",
        "build",
        "flags.prop",
    )

    write_if_diff(
        output,
        text,
    )


# ============================================================
# APK
# ============================================================

def build_apk(module):

    ensure_jdk()

    dump_flags_app()

    config_path = (
        Path(args.config)
        .resolve()
    )

    old_dir = Path.cwd()

    build_type = (
        "Release"
        if args.release
        else "Debug"
    )

    try:

        os.chdir("app")

        gradlew = Path(
            paths().gradlew
        )

        if not gradlew.exists():

            error(
                "Gradle wrapper not found: "
                f"{gradlew}"
            )

            return None

        command = [
            gradlew,
            f"{module}:assemble{build_type}",
            f"-PconfigPath={config_path}",
        ]

        result = execv(command)

        if result.returncode != 0:

            error(
                f"Build {module} failed!"
            )

            return None

    finally:

        os.chdir(old_dir)

    build_type_lower = (
        build_type.lower()
    )

    module_parts = [
        p
        for p in module.split(":")
        if p
    ]

    module_name = module_parts[-1]

    apk_name = (
        f"{module_name}-"
        f"{build_type_lower}.apk"
    )

    source = (
        Path("app")
        / Path(*module_parts)
        / "build"
        / "outputs"
        / "apk"
        / build_type_lower
        / apk_name
    )

    destination = (
        Path(config["outdir"])
        / apk_name
    )

    if not mv(
        source,
        destination,
    ):

        error(
            "APK was not generated: "
            f"{source}"
        )

        return None

    return destination


# ============================================================
# APP
# ============================================================

def build_app():

    header(
        "* Building Magisk app"
    )

    apk = build_apk(":apk")

    if apk is None:
        return

    app_name = apk.name.replace(
        "apk-",
        "app-",
        1,
    )

    app_output = (
        apk.parent
        / app_name
    )

    mv(
        apk,
        app_output,
    )

    header(
        f"Output: {app_output}"
    )

    build_type = (
        "release"
        if args.release
        else "debug"
    )

    stub_source = Path(
        "app",
        "core",
        "src",
        build_type,
        "assets",
        "stub.apk",
    )

    stub_output = (
        Path(config["outdir"])
        / f"stub-{build_type}.apk"
    )

    cp(
        stub_source,
        stub_output,
    )


def build_app_ng():

    header(
        "* Building next generation Magisk app"
    )

    apk = build_apk(":apk-ng")

    if apk:
        header(
            f"Output: {apk}"
        )


def build_stub():

    header(
        "* Building stub app"
    )

    apk = build_apk(":stub")

    if apk:
        header(
            f"Output: {apk}"
        )


def build_test():

    old_release = args.release

    args.release = True

    try:

        header(
            "* Building test app"
        )

        apk = build_apk(":test")

        if apk is None:
            return

        output = (
            apk.parent
            / "test.apk"
        )

        mv(
            apk,
            output,
        )

        header(
            f"Output: {output}"
        )

    finally:

        args.release = old_release


# ============================================================
# BUILD ALL
# ============================================================

def build_all():

    build_native()
    build_app()
    build_app_ng()
    build_test()


# ============================================================
# CLEAN
# ============================================================

def cleanup():

    requested = getattr(
        args,
        "targets",
        None,
    )

    if requested:

        targets = (
            set(requested)
            & CLEAN_TARGETS
        )

        invalid = (
            set(requested)
            - CLEAN_TARGETS
        )

        if invalid:

            error(
                "Unknown clean target(s): "
                + ", ".join(
                    sorted(invalid)
                )
            )

    else:

        targets = set(
            CLEAN_TARGETS
        )

    if "native" in targets:

        targets.add("cpp")
        targets.add("rust")

    if "cpp" in targets:

        header(
            "* Cleaning C++"
        )

        rm_rf(
            Path(
                "native",
                "libs",
            )
        )

        rm_rf(
            Path(
                "native",
                "obj",
            )
        )

    if "rust" in targets:

        header(
            "* Cleaning Rust"
        )

        rm_rf(
            Path(
                "native",
                "out",
                "rust",
            )
        )

        rm(
            Path(
                "native",
                "src",
                "boot",
                "proto",
                "mod.rs",
            )
        )

        rm(
            Path(
                "native",
                "src",
                "boot",
                "proto",
                "update_metadata.rs",
            )
        )

        for generated in glob.glob(
            "native/**/*-rs.*pp",
            recursive=True,
        ):

            rm(
                Path(generated)
            )

    if "native" in targets:

        header(
            "* Cleaning native"
        )

        rm_rf(
            Path(
                "native",
                "out",
            )
        )

        rm_rf(
            Path(
                "tools",
                "elf-cleaner",
                "target",
            )
        )

    if "app" in targets:

        ensure_jdk()

        header(
            "* Cleaning app"
        )

        old_dir = Path.cwd()

        try:

            os.chdir("app")

            result = execv(
                [
                    paths().gradlew,
                    ":clean",
                ]
            )

            if result.returncode != 0:

                error(
                    "Gradle clean failed!"
                )

        finally:

            os.chdir(old_dir)


# ============================================================
# IDE
# ============================================================

def gen_ide():

    ensure_cargo()

    os.environ.pop(
        "NDK_CCACHE",
        None,
    )

    dump_flags_native()
    dump_flags_app()

    abi = getattr(
        args,
        "abi",
        None,
    )

    if not abi:

        for candidate in build_abis:

            if "64" in candidate:

                abi = candidate
                break

        if not abi:

            abi = next(
                iter(build_abis)
            )

    abi = ABI_ALIAS.get(
        abi,
        abi,
    )

    set_build_abis(
        {abi}
    )

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        result = execv(
            [
                "cargo",
                "check",
                "--target",
                build_abis[abi],
            ]
        )

        if result.returncode != 0:
            error("cargo check failed!")

    finally:

        os.chdir(old_dir)

    rm(
        Path(
            "native",
            "compile_commands.json",
        )
    )

    run_ndk_build(
        [
            "B_MAGISK=1",
            "B_INIT=1",
            "B_BOOT=1",
            "B_POLICY=1",
            "B_PRELOAD=1",
            "B_PROP=1",
            "B_CRT0=1",
            "compile_commands.json",
        ]
    )


# ============================================================
# CLIPPY
# ============================================================

def clippy_cli():

    ensure_cargo()

    global force_out

    force_out = True

    abi_args = getattr(
        args,
        "abi",
        None,
    )

    if abi_args:

        set_build_abis(
            set(abi_args)
        )

    else:

        set_build_abis(
            set(DEFAULT_ABIS)
        )

    debug = args.debug
    release = args.release

    if not debug and not release:

        debug = True
        release = True

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        for triple in build_abis.values():

            if debug:

                result = execv(
                    [
                        "cargo",
                        "clippy",
                        "--no-deps",
                        "--target",
                        triple,
                    ]
                )

                if result.returncode != 0:
                    error(
                        f"Clippy debug failed: {triple}"
                    )

            if release:

                result = execv(
                    [
                        "cargo",
                        "clippy",
                        "--no-deps",
                        "--target",
                        triple,
                        "--release",
                    ]
                )

                if result.returncode != 0:
                    error(
                        f"Clippy release failed: {triple}"
                    )

    finally:

        os.chdir(old_dir)


# ============================================================
# CARGO
# ============================================================

def cargo_cli():

    ensure_cargo()

    global force_out

    force_out = True

    commands = list(
        getattr(
            args,
            "commands",
            [],
        )
    )

    if commands and commands[0] == "--":
        commands.pop(0)

    if not commands:

        commands = [
            "--version"
        ]

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        result = execv(
            [
                "cargo",
                *commands,
            ]
        )

        if result.returncode != 0:

            error(
                "Cargo command failed!"
            )

    finally:

        os.chdir(old_dir)


# ============================================================
# RUSTUP
# ============================================================

def setup_rustup():

    wrapper_dir = Path(
        args.wrapper_dir
    )

    rm_rf(wrapper_dir)

    wrapper_dir.mkdir(
        mode=0o755,
        parents=True,
        exist_ok=True,
    )

    cargo_home = Path(
        os.environ.get(
            "CARGO_HOME",
            Path.home() / ".cargo",
        )
    )

    cargo_bin = (
        cargo_home
        / "bin"
    )

    if not cargo_bin.exists():

        error(
            f"Cargo bin directory not found: {cargo_bin}"
        )

        return

    for source in cargo_bin.iterdir():

        target = (
            wrapper_dir
            / source.name
        )

        try:

            target.symlink_to(
                f"rustup{EXE_EXT}"
            )

        except FileExistsError:
            pass

    wrapper_src = Path(
        "tools",
        "rustup-wrapper",
    )

    cargo_toml = (
        wrapper_src
        / "Cargo.toml"
    )

    result = execv(
        [
            "cargo",
            "build",
            "--release",
            f"--manifest-path={cargo_toml}",
        ]
    )

    if result.returncode != 0:

        error(
            "rustup-wrapper build failed!"
        )

        return

    wrapper = (
        wrapper_dir
        / f"rustup{EXE_EXT}"
    )

    wrapper.unlink(
        missing_ok=True
    )

    wrapper_binary = (
        wrapper_src
        / "target"
        / "release"
        / f"rustup-wrapper{EXE_EXT}"
    )

    if not wrapper_binary.exists():

        error(
            f"rustup wrapper not found: {wrapper_binary}"
        )

        return

    cp(
        wrapper_binary,
        wrapper,
    )

    try:
        wrapper.chmod(0o755)
    except OSError:
        pass


# ============================================================
# ADB
# ============================================================

@functools.cache
def adb_path():

    try:

        cached = paths().adb

        if cached:
            return Path(cached)

    except Exception:
        pass

    adb = shutil.which(
        "adb"
    )

    if adb:
        return Path(adb)

    error(
        "Command 'adb' cannot be found in PATH"
    )

    return None


def push_files(script: Path):

    script = Path(script)

    if not script.exists():

        error(
            f"Script not found: {script}"
        )

        return

    if args.build:
        build_all()

    adb = adb_path()

    if adb is None:
        return

    abi = cmd_out(
        [
            adb,
            "shell",
            "getprop",
            "ro.product.cpu.abi",
        ]
    )

    abi = abi.strip()

    if not abi:

        error(
            "Cannot detect device ABI"
        )

        return

    if args.apk:

        apk = Path(
            args.apk
        )

    else:

        filename = (
            "app-release.apk"
            if args.release
            else "app-debug.apk"
        )

        apk = (
            Path(config["outdir"])
            / filename
        )

    if not apk.exists():

        error(
            f"APK not found: {apk}"
        )

        return

    busybox = (
        Path(config["outdir"])
        / "busybox"
    )

    try:

        with ZipFile(apk) as archive:

            library = (
                f"lib/{abi}/libbusybox.so"
            )

            if library not in archive.namelist():

                error(
                    f"BusyBox library not found in APK: {library}"
                )

                return

            with archive.open(
                library
            ) as source:

                with open(
                    busybox,
                    "wb",
                ) as destination:

                    shutil.copyfileobj(
                        source,
                        destination,
                    )

        result = execv(
            [
                adb,
                "push",
                busybox,
                script,
                "/data/local/tmp",
            ]
        )

        if result.returncode != 0:

            error(
                "adb push failed!"
            )

            return

    finally:

        rm(busybox)

    result = execv(
        [
            adb,
            "push",
            apk,
            "/data/local/tmp/magisk.apk",
        ]
    )

    if result.returncode != 0:

        error(
            "adb push APK failed!"
        )


# ============================================================
# EMULATOR
# ============================================================

def setup_avd():

    header(
        "* Setting up emulator"
    )

    push_files(
        Path(
            "scripts",
            "live_setup.sh",
        )
    )

    adb = adb_path()

    if adb is None:
        return

    result = execv(
        [
            adb,
            "shell",
            "sh",
            "/data/local/tmp/live_setup.sh",
        ]
    )

    if result.returncode != 0:

        error(
            "live_setup.sh failed!"
        )


def patch_avd_file():

    input_file = Path(
        args.image
    )

    output_file = Path(
        args.output
    )

    if not input_file.exists():

        error(
            f"Input image not found: {input_file}"
        )

        return

    header(
        f"* Patching {input_file.name}"
    )

    push_files(
        Path(
            "scripts",
            "host_patch.sh",
        )
    )

    adb = adb_path()

    if adb is None:
        return

    result = execv(
        [
            adb,
            "push",
            input_file,
            "/data/local/tmp",
        ]
    )

    if result.returncode != 0:

        error(
            "adb push image failed!"
        )

        return

    source = (
        "/data/local/tmp/"
        + input_file.name
    )

    patched = (
        source
        + ".magisk"
    )

    result = execv(
        [
            adb,
            "shell",
            "sh",
            "/data/local/tmp/host_patch.sh",
            source,
        ]
    )

    if result.returncode != 0:

        error(
            "host_patch.sh failed!"
        )

        return

    result = execv(
        [
            adb,
            "pull",
            patched,
            output_file,
        ]
    )

    if result.returncode != 0:

        error(
            "adb pull failed!"
        )

        return

    header(
        f"Output: {output_file}"
    )


# ============================================================
# CONFIG
# ============================================================

def parse_props(file):

    file = Path(file)

    result = {}

    if not file.exists():
        return result

    try:

        lines = file.read_text(
            encoding="utf-8"
        ).splitlines()

    except OSError:

        return result

    for line in lines:

        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1,
        )

        key = key.strip()
        value = value.strip()

        if key and value:
            result[key] = value

    return result


def set_build_abis(abis):

    global build_abis

    normalized = set()

    for abi in abis:

        abi = str(abi).strip()

        if not abi:
            continue

        normalized.add(
            ABI_ALIAS.get(
                abi,
                abi,
            )
        )

    unknown = (
        normalized
        - set(SUPPORT_ABIS)
    )

    if unknown:

        error(
            "Unknown ABI: "
            + ", ".join(
                sorted(unknown)
            )
        )

        return

    build_abis = {
        abi: SUPPORT_ABIS[abi]
        for abi in sorted(normalized)
    }


def load_config():

    commit = cmd_out(
        [
            "git",
            "rev-parse",
            "--short=8",
            "HEAD",
        ]
    )

    config.clear()

    config["version"] = (
        commit
        if commit
        else "local"
    )

    config["versionCode"] = 1000000

    config["outdir"] = Path(
        "out"
    )

    if args.config.exists():

        config.update(
            parse_props(
                args.config
            )
        )

    gradle_properties = Path(
        "app",
        "gradle.properties",
    )

    gradle_config = parse_props(
        gradle_properties
    )

    for key, value in gradle_config.items():

        if key.startswith(
            "magisk."
        ):

            config[
                key[7:]
            ] = value

    try:

        config["versionCode"] = int(
            config["versionCode"]
        )

    except (
        TypeError,
        ValueError,
    ):

        error(
            'Config error: '
            '"versionCode" must be an integer'
        )

        config["versionCode"] = 1000000

    config["outdir"] = Path(
        config["outdir"]
    )

    config["outdir"].mkdir(
        mode=0o755,
        parents=True,
        exist_ok=True,
    )

    if "abiList" in config:

        abis = {
            x.strip()
            for x in re.split(
                r"[\s,]+",
                str(config["abiList"]),
            )
            if x.strip()
        }

    else:

        abis = set(
            DEFAULT_ABIS
        )

    set_build_abis(
        abis
    )


# ============================================================
# ARGUMENTS
# ============================================================

def add_common_arguments(parser):

    parser.add_argument(
        "-r",
        "--release",
        action="store_true",
        help="compile in release mode",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity",
    )

    parser.add_argument(
        "-c",
        "--config",
        default="config.prop",
        help="configuration file",
    )


def parse_args():

    parser = argparse.ArgumentParser(
        description="Magisk build script"
    )

    add_common_arguments(
        parser
    )

    subparsers = parser.add_subparsers(
        dest="action",
        title="actions",
        required=True,
    )

    # --------------------------------------------------------
    # ALL
    # --------------------------------------------------------

    all_parser = subparsers.add_parser(
        "all",
        help="build everything",
    )

    # --------------------------------------------------------
    # NATIVE
    # --------------------------------------------------------

    native_parser = subparsers.add_parser(
        "native",
        help="build native binaries",
    )

    native_parser.add_argument(
        "targets",
        nargs="*",
        choices=sorted(
            SUPPORT_TARGETS
        ),
        help="native build targets",
    )

    # --------------------------------------------------------
    # APP
    # --------------------------------------------------------

    subparsers.add_parser(
        "app",
        help="build Magisk app",
    )

    subparsers.add_parser(
        "app-ng",
        help="build next generation app",
    )

    subparsers.add_parser(
        "stub",
        help="build stub app",
    )

    subparsers.add_parser(
        "test",
        help="build test app",
    )

    # --------------------------------------------------------
    # CLEAN
    # --------------------------------------------------------

    clean_parser = subparsers.add_parser(
        "clean",
        help="clean build files",
    )

    clean_parser.add_argument(
        "targets",
        nargs="*",
        choices=sorted(
            CLEAN_TARGETS
        ),
        help="clean targets",
    )

    # --------------------------------------------------------
    # NDK
    # --------------------------------------------------------

    subparsers.add_parser(
        "ndk",
        help="download/setup Magisk NDK",
    )

    # --------------------------------------------------------
    # EMULATOR
    # --------------------------------------------------------

    emulator_parser = subparsers.add_parser(
        "emulator",
        help="setup AVD",
    )

    emulator_parser.add_argument(
        "apk",
        nargs="?",
        help="Magisk APK",
    )

    emulator_parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="build before setup",
    )

    # --------------------------------------------------------
    # AVD PATCH
    # --------------------------------------------------------

    avd_parser = subparsers.add_parser(
        "avd_patch",
        help="patch AVD image",
    )

    avd_parser.add_argument(
        "image",
        help="input AVD image",
    )

    avd_parser.add_argument(
        "output",
        help="output patched image",
    )

    avd_parser.add_argument(
        "--apk",
        help="APK to use",
    )

    avd_parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="build before patching",
    )

    # --------------------------------------------------------
    # CARGO
    # --------------------------------------------------------

    cargo_parser = subparsers.add_parser(
        "cargo",
        help="run Cargo command",
    )

    cargo_parser.add_argument(
        "commands",
        nargs=argparse.REMAINDER,
    )

    # --------------------------------------------------------
    # CLIPPY
    # --------------------------------------------------------

    clippy_parser = subparsers.add_parser(
        "clippy",
        help="run Rust Clippy",
    )

    clippy_parser.add_argument(
        "--abi",
        action="append",
        help="ABI to check",
    )

    clippy_parser.add_argument(
        "-r",
        "--release",
        action="store_true",
    )

    clippy_parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
    )

    # --------------------------------------------------------
    # RUSTUP
    # --------------------------------------------------------

    rustup_parser = subparsers.add_parser(
        "rustup",
        help="setup rustup wrapper",
    )

    rustup_parser.add_argument(
        "wrapper_dir",
        help="wrapper output directory",
    )

    # --------------------------------------------------------
    # GEN
    # --------------------------------------------------------

    gen_parser = subparsers.add_parser(
        "gen",
        help="generate IDE files",
    )

    gen_parser.add_argument(
        "--abi",
        help="ABI",
    )

    return parser.parse_args()


# ============================================================
# DISPATCH
# ============================================================

def dispatch():

    action = args.action

    if action == "all":
        build_all()

    elif action == "native":
        build_native()

    elif action == "app":
        build_app()

    elif action == "app-ng":
        build_app_ng()

    elif action == "stub":
        build_stub()

    elif action == "test":
        build_test()

    elif action == "clean":
        cleanup()

    elif action == "ndk":
        setup_ndk()

    elif action == "emulator":
        setup_avd()

    elif action == "avd_patch":
        patch_avd_file()

    elif action == "cargo":
        cargo_cli()

    elif action == "clippy":
        clippy_cli()

    elif action == "rustup":
        setup_rustup()

    elif action == "gen":
        gen_ide()

    else:
        error(
            f"Unknown action: {action}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    global args

    args = parse_args()

    args.config = Path(
        args.config
    )

    try:

        load_config()

        dispatch()

    except KeyboardInterrupt:

        print(
            "\nBuild interrupted."
        )

        return 130

    except Exception as exc:

        error(
            f"Build failed: {exc}"
        )

        return 1

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
        )
