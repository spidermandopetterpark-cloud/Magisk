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

support_abis = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "thumbv7neon-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
    "riscv64": "riscv64-linux-android",
}

abi_alias = {
    "arm": "armeabi-v7a",
    "arm32": "armeabi-v7a",
    "arm64": "arm64-v8a",
    "x64": "x86_64",
}

default_abis = support_abis.keys() - {"riscv64"}

support_targets = {
    "magisk",
    "magiskinit",
    "magiskboot",
    "magiskpolicy",
    "resetprop",
}

default_targets = support_targets - {"resetprop"}

rust_targets = default_targets.copy()

clean_targets = {
    "native",
    "cpp",
    "rust",
    "app",
}


# ============================================================
# GLOBALS
# ============================================================

config = {}
args: argparse.Namespace
build_abis: dict[str, str] = {}
force_out = False


# ============================================================
# HELPERS
# ============================================================

def vprint(text):
    if args.verbose > 0:
        print(text)


def mv(source: Path, target: Path):
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        vprint(f"mv {source} -> {target}")
    except FileNotFoundError:
        pass


def cp(source: Path, target: Path):
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(target))
        vprint(f"cp {source} -> {target}")
    except FileNotFoundError:
        pass


def rm(file: Path):
    try:
        os.remove(file)
        vprint(f"rm {file}")
    except FileNotFoundError:
        pass


def rm_on_error(func, path, _):
    try:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)
    except FileNotFoundError:
        pass


def rm_rf(path: Path):
    if not path.exists():
        return

    vprint(f"rm -rf {path}")

    if sys.version_info >= (3, 12):
        shutil.rmtree(
            path,
            ignore_errors=False,
            onexc=rm_on_error,
        )
    else:
        shutil.rmtree(
            path,
            ignore_errors=False,
            onerror=rm_on_error,
        )


def execv(cmds: list):
    out = (
        None
        if force_out or args.verbose > 0
        else subprocess.DEVNULL
    )

    return subprocess.run(
        cmds,
        stdout=out,
        shell=is_windows,
    )


def cmd_out(cmds: list):
    result = subprocess.run(
        cmds,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=is_windows,
    )

    return result.stdout.strip().decode("utf-8")


# ============================================================
# NDK
# ============================================================

def setup_ndk():
    """
    Download and install the exact NDK expected by this project.
    """

    ndk_parent = paths().ndk.parent
    ndk_parent.mkdir(parents=True, exist_ok=True)

    url = (
        f"https://github.com/topjohnwu/ondk/releases/download/"
        f"{ondk_version}/"
        f"ondk-{ondk_version}-{os_name}.tar.xz"
    )

    ndk_archive = url.split("/")[-1]

    ondk_path = ndk_parent / f"ondk-{ondk_version}"

    header(f"* Downloading {ndk_archive}")

    rm_rf(ondk_path)

    try:
        with urllib.request.urlopen(url) as response:
            with tarfile.open(
                mode="r|xz",
                fileobj=response,
            ) as tar:

                if hasattr(tarfile, "data_filter"):
                    tar.extractall(
                        ndk_parent,
                        filter="tar",
                    )
                else:
                    tar.extractall(ndk_parent)

    except Exception as e:
        error(f"Failed to download NDK: {e}")
        return

    rm_rf(paths().ndk)

    mv(
        ondk_path,
        paths().ndk,
    )

    if not paths().ndk.exists():
        error(
            "NDK installation failed. "
            f"Expected: {paths().ndk}"
        )

    header("* NDK installed successfully")


def ensure_project_ndk():
    """
    Make sure the project NDK exists.
    """

    try:
        ndk = paths().ndk
    except Exception:
        ndk = None

    if ndk is None or not Path(ndk).exists():
        header("! Project NDK not found")
        header("! Installing project NDK...")
        setup_ndk()

    if not Path(paths().ndk).exists():
        error(
            "Project NDK is still missing after installation."
        )


# ============================================================
# NATIVE BUILD
# ============================================================

def clean_elf():
    ensure_cargo()

    cargo_toml = Path(
        "tools",
        "elf-cleaner",
        "Cargo.toml",
    )

    cmds = [
        "cargo",
        "run",
        "--release",
        "--manifest-path",
        str(cargo_toml),
    ]

    if args.verbose == 0:
        cmds.append("-q")
    elif args.verbose > 1:
        cmds.append("--verbose")

    cmds.append("--")

    cmds.extend(
        glob.glob("native/out/*/magisk")
    )

    cmds.extend(
        glob.glob("native/out/*/magiskpolicy")
    )

    proc = execv(cmds)

    if proc.returncode != 0:
        error("ELF cleaner failed!")


def collect_ndk_build():
    for arch in build_abis.keys():

        arch_dir = Path(
            "native",
            "libs",
            arch,
        )

        out_dir = Path(
            "native",
            "out",
            arch,
        )

        out_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not arch_dir.exists():
            continue

        for source in arch_dir.iterdir():

            target = out_dir / source.name

            mv(
                source,
                target,
            )


def run_ndk_build(cmds: list[str]):

    ensure_project_ndk()

    old_dir = Path.cwd()

    try:
        os.chdir("native")

        cmds = list(cmds)

        cmds.append("NDK_PROJECT_PATH=.")
        cmds.append(
            "NDK_APPLICATION_MK=src/Application.mk"
        )

        cmds.append(
            f"APP_ABI={' '.join(build_abis.keys())}"
        )

        cmds.append(
            f"-j{cpu_count}"
        )

        if args.verbose > 1:
            cmds.append("V=1")

        if not args.release:
            cmds.append("MAGISK_DEBUG=1")

        proc = execv(
            [
                str(paths().ndk_build),
                *cmds,
            ]
        )

        if proc.returncode != 0:
            error("Build binary failed!")

    finally:
        os.chdir(old_dir)


def build_cpp_src(targets: set[str]):

    cmds = []
    clean = False

    if "magisk" in targets:
        cmds.append("B_MAGISK=1")
        clean = True

    if "magiskpolicy" in targets:
        cmds.append("B_POLICY=1")
        clean = True

    if "magiskinit" in targets:
        cmds.append("B_PRELOAD=1")

    if "resetprop" in targets:
        cmds.append("B_PROP=1")

    if cmds:

        run_ndk_build(cmds)

        collect_ndk_build()

    cmds.clear()

    if "magiskinit" in targets:
        cmds.append("B_INIT=1")

    if "magiskboot" in targets:
        cmds.append("B_BOOT=1")

    if cmds:

        cmds.append("B_CRT0=1")

        run_ndk_build(cmds)

        collect_ndk_build()

    if clean:
        clean_elf()


def build_rust_src(targets: set[str]):

    ensure_cargo()

    targets = targets.copy()

    if "resetprop" in targets:
        targets.add("magisk")

    targets = targets & rust_targets

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

        cmds = [
            "cargo",
            "build",
            "-p",
            "",
        ]

        if args.release:
            cmds.append("-r")
            profile = "release"
        else:
            profile = "debug"

        if args.verbose == 0:
            cmds.append("-q")
        elif args.verbose > 1:
            cmds.append("--verbose")

        for triple in build_abis.values():

            cmds.append("--target")
            cmds.append(triple)

        for tgt in targets:

            cmds[3] = tgt

            proc = execv(cmds)

            if proc.returncode != 0:
                error(
                    f"Rust build failed: {tgt}"
                )

    finally:
        os.chdir(old_dir)

    native_out = Path(
        "native",
        "out",
    )

    rust_out = native_out / "rust"

    for arch, triple in build_abis.items():

        arch_out = native_out / arch

        arch_out.mkdir(
            mode=0o755,
            exist_ok=True,
        )

        for tgt in targets:

            source = (
                rust_out
                / triple
                / profile
                / f"lib{tgt}.a"
            )

            target = (
                arch_out
                / f"lib{tgt}-rs.a"
            )

            mv(
                source,
                target,
            )


def write_if_diff(
    file_name: Path,
    text: str,
):

    do_write = True

    if file_name.exists():

        with open(
            file_name,
            "r",
        ) as f:
            orig = f.read()

        do_write = orig != text

    if do_write:

        file_name.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            file_name,
            "w",
        ) as f:
            f.write(text)


def dump_flags_native():

    flag_txt = "#pragma once\n"

    flag_txt += (
        f'#define MAGISK_VERSION      '
        f'"{config["version"]}"\n'
    )

    flag_txt += (
        f'#define MAGISK_VER_CODE     '
        f'{config["versionCode"]}\n'
    )

    flag_txt += (
        f'#define MAGISK_DEBUG        '
        f'{0 if args.release else 1}\n'
    )

    native_gen_path = Path(
        "native",
        "out",
        "generated",
    )

    native_gen_path.mkdir(
        mode=0o755,
        parents=True,
        exist_ok=True,
    )

    write_if_diff(
        native_gen_path / "flags.h",
        flag_txt,
    )

    rust_flag_txt = (
        f'pub const MAGISK_VERSION: &str = '
        f'"{config["version"]}";\n'
    )

    rust_flag_txt += (
        f'pub const MAGISK_VER_CODE: i32 = '
        f'{config["versionCode"]};\n'
    )

    write_if_diff(
        native_gen_path / "flags.rs",
        rust_flag_txt,
    )


def build_native():

    ensure_project_ndk()
    ensure_toolchain()

    if (
        "targets" not in vars(args)
        or not args.targets
    ):
        targets = default_targets
    else:

        targets = (
            set(args.targets)
            & support_targets
        )

        if not targets:
            return

    header(
        "* Building: "
        + " ".join(sorted(targets))
    )

    dump_flags_native()

    build_rust_src(targets)

    build_cpp_src(targets)


# ============================================================
# APP
# ============================================================

def dump_flags_app():

    flag_txt = (
        f"abiList={','.join(build_abis.keys())}\n"
    )

    flag_txt += (
        f"version={config['version']}\n"
    )

    flag_txt += (
        f"versionCode={config['versionCode']}\n"
    )

    app_build_dir = Path(
        "app",
        "build",
    )

    app_build_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_if_diff(
        app_build_dir / "flags.prop",
        flag_txt,
    )


def build_apk(module: str):

    ensure_jdk()

    dump_flags_app()

    config_path = args.config.resolve()

    old_dir = Path.cwd()

    try:

        os.chdir("app")

        build_type = (
            "Release"
            if args.release
            else "Debug"
        )

        proc = execv(
            [
                paths().gradlew,
                f"{module}:assemble{build_type}",
                f"-PconfigPath={config_path}",
            ]
        )

        if proc.returncode != 0:
            error(
                f"Build {module} failed!"
            )

    finally:
        os.chdir(old_dir)

    build_type = build_type.lower()

    module_paths = module.split(":")

    apk = (
        f"{module_paths[-1]}-"
        f"{build_type}.apk"
    )

    source = Path(
        "app",
        *module_paths,
        "build",
        "outputs",
        "apk",
        build_type,
        apk,
    )

    target = (
        config["outdir"]
        / apk
    )

    mv(
        source,
        target,
    )

    return target


def build_app():

    header(
        "* Building the Magisk app"
    )

    apk = build_apk(":apk")

    build_type = (
        "release"
        if args.release
        else "debug"
    )

    source = apk

    target = (
        apk.parent
        / apk.name.replace(
            "apk-",
            "app-",
        )
    )

    mv(
        source,
        target,
    )

    header(
        f"Output: {target}"
    )

    source = Path(
        "app",
        "core",
        "src",
        build_type,
        "assets",
        "stub.apk",
    )

    target = (
        config["outdir"]
        / f"stub-{build_type}.apk"
    )

    cp(
        source,
        target,
    )


def build_app_ng():

    header(
        "* Building the next generation Magisk app"
    )

    apk = build_apk(":apk-ng")

    header(
        f"Output: {apk}"
    )


def build_stub():

    header(
        "* Building the stub app"
    )

    apk = build_apk(":stub")

    header(
        f"Output: {apk}"
    )


def build_test():

    old_release = args.release

    args.release = True

    try:

        header(
            "* Building the test app"
        )

        source = build_apk(":test")

        target = (
            source.parent
            / "test.apk"
        )

        mv(
            source,
            target,
        )

        header(
            f"Output: {target}"
        )

    finally:
        args.release = old_release


# ============================================================
# CLEAN
# ============================================================

def cleanup():

    if args.targets:

        targets = (
            set(args.targets)
            & clean_targets
        )

        if "native" in targets:
            targets.add("cpp")
            targets.add("rust")

    else:

        targets = clean_targets

    if "cpp" in targets:

        header("* Cleaning C++")

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

        header("* Cleaning Rust")

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

        for rs_gen in glob.glob(
            "native/**/*-rs.*pp",
            recursive=True,
        ):
            rm(Path(rs_gen))

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

        header("* Cleaning app")

        old_dir = Path.cwd()

        try:
            os.chdir("app")
            execv(
                [
                    paths().gradlew,
                    ":clean",
                ]
            )
        finally:
            os.chdir(old_dir)


# ============================================================
# BUILD ALL
# ============================================================

def build_all():

    build_native()
    build_app()
    build_app_ng()
    build_test()


# ============================================================
# IDE
# ============================================================

def gen_ide():

    ensure_cargo()

    if "NDK_CCACHE" in os.environ:
        os.environ.pop("NDK_CCACHE")

    dump_flags_native()
    dump_flags_app()

    if not args.abi:

        for abi in build_abis.keys():

            if "64" in abi:

                args.abi = abi
                break

        if not args.abi:
            args.abi = next(
                iter(build_abis.keys())
            )

    set_build_abis(
        {args.abi}
    )

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        execv(
            [
                "cargo",
                "check",
                "--target",
                build_abis[args.abi],
            ]
        )

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

    if args.abi:
        set_build_abis(
            set(args.abi)
        )
    else:
        set_build_abis(
            default_abis
        )

    if (
        not args.release
        and not args.debug
    ):

        args.release = True
        args.debug = True

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        cmds = [
            "cargo",
            "clippy",
            "--no-deps",
            "--target",
        ]

        for triple in build_abis.values():

            if args.debug:

                execv(
                    cmds + [triple]
                )

            if args.release:

                execv(
                    cmds
                    + [
                        triple,
                        "--release",
                    ]
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

    if (
        len(args.commands) >= 1
        and args.commands[0] == "--"
    ):
        args.commands = args.commands[1:]

    old_dir = Path.cwd()

    try:

        os.chdir(
            Path(
                "native",
                "src",
            )
        )

        execv(
            [
                "cargo",
                *args.commands,
            ]
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

    if "CARGO_HOME" in os.environ:
        cargo_home = Path(
            os.environ["CARGO_HOME"]
        )
    else:
        cargo_home = (
            Path.home()
            / ".cargo"
        )

    cargo_bin = (
        cargo_home
        / "bin"
    )

    for src in cargo_bin.iterdir():

        tgt = (
            wrapper_dir
            / src.name
        )

        tgt.symlink_to(
            f"rustup{EXE_EXT}"
        )

    wrapper_src = Path(
        "tools",
        "rustup-wrapper",
    )

    cargo_toml = (
        wrapper_src
        / "Cargo.toml"
    )

    cmds = [
        "cargo",
        "build",
        "--release",
        f"--manifest-path={cargo_toml}",
    ]

    if args.verbose > 1:
        cmds.append("--verbose")

    execv(cmds)

    wrapper = (
        wrapper_dir
        / f"rustup{EXE_EXT}"
    )

    wrapper.unlink(
        missing_ok=True
    )

    cp(
        wrapper_src
        / "target"
        / "release"
        / f"rustup-wrapper{EXE_EXT}",
        wrapper,
    )

    wrapper.chmod(0o755)


# ============================================================
# ADB
# ============================================================

@functools.cache
def adb_path():

    if paths.cache_info().currsize > 1:
        return paths().adb

    adb = shutil.which("adb")

    if adb:
        return Path(adb)

    error(
        "Command 'adb' cannot be found in PATH"
    )


def push_files(script: Path):

    if args.build:
        build_all()

    abi = cmd_out(
        [
            adb_path(),
            "shell",
            "getprop",
            "ro.product.cpu.abi",
        ]
    )

    if not abi:
        error(
            "Cannot detect emulator ABI"
        )

    if args.apk:
        apk = Path(args.apk)
    else:

        name = (
            "app-release.apk"
            if args.release
            else "app-debug.apk"
        )

        apk = (
            Path(config["outdir"])
            / name
        )

    busybox = (
        Path(config["outdir"])
        / "busybox"
    )

    with ZipFile(apk) as zf:

        with zf.open(
            f"lib/{abi}/libbusybox.so"
        ) as libbb:

            with open(
                busybox,
                "wb",
            ) as bb:

                bb.write(
                    libbb.read()
                )

    try:

        proc = execv(
            [
                adb_path(),
                "push",
                busybox,
                script,
                "/data/local/tmp",
            ]
        )

        if proc.returncode != 0:
            error(
                "adb push failed!"
            )

    finally:
        rm(busybox)

    proc = execv(
        [
            adb_path(),
            "push",
            apk,
            "/data/local/tmp/magisk.apk",
        ]
    )

    if proc.returncode != 0:
        error(
            "adb push failed!"
        )


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

    proc = execv(
        [
            adb_path(),
            "shell",
            "sh",
            "/data/local/tmp/live_setup.sh",
        ]
    )

    if proc.returncode != 0:
        error(
            "live_setup.sh failed!"
        )


def patch_avd_file():

    input_file = Path(
        args.image
    )

    output = Path(
        args.output
    )

    header(
        f"* Patching {input_file.name}"
    )

    push_files(
        Path(
            "scripts",
            "host_patch.sh",
        )
    )

    proc = execv(
        [
            adb_path(),
            "push",
            input_file,
            "/data/local/tmp",
        ]
    )

    if proc.returncode != 0:
        error(
            "adb push failed!"
        )

    src_file = (
        f"/data/local/tmp/"
        f"{input_file.name}"
    )

    out_file = (
        f"{src_file}.magisk"
    )

    proc = execv(
        [
            adb_path(),
            "shell",
            "sh",
            "/data/local/tmp/host_patch.sh",
            src_file,
        ]
    )

    if proc.returncode != 0:
        error(
            "host_patch.sh failed!"
        )

    proc = execv(
        [
            adb_path(),
            "pull",
            out_file,
            output,
        ]
    )

    if proc.returncode != 0:
        error(
            "adb pull failed!"
        )

    header(
        f"Output: {output}"
    )


# ============================================================
# CONFIG
# ============================================================

def parse_props(
    file: Path,
) -> dict[str, str]:

    props = {}

    if not file.exists():
        return props

    with open(
        file,
        "r",
    ) as f:

        for line in f:

            line = line.strip(
                " \t\r\n"
            )

            if (
                line.startswith("#")
                or not line
            ):
                continue

            prop = line.split(
                "=",
                1,
            )

            if len(prop) != 2:
                continue

            key = prop[0].strip()
            value = prop[1].strip()

            if not key or not value:
                continue

            props[key] = value

    return props


def set_build_abis(
    abis: set[str],
):

    global build_abis

    abis = {
        abi_alias.get(k, k)
        for k in abis
    }

    unknown = (
        abis
        - support_abis.keys()
    )

    if unknown:

        error(
            "Unknown ABI: "
            + ", ".join(sorted(unknown))
        )

    build_abis = {
        k: support_abis[k]
        for k in abis
        if k in support_abis
    }


def load_config():

    commit_hash = cmd_out(
        [
            "git",
            "rev-parse",
            "--short=8",
            "HEAD",
        ]
    )

    config["version"] = (
        commit_hash
        if commit_hash
        else "local"
    )

    config["versionCode"] = 1000000
    config["outdir"] = Path("out")

    if args.config.exists():

        config.update(
            parse_props(
                args.config
            )
        )

    gradle_props = Path(
        "app",
        "gradle.properties",
    )

    for key, value in parse_props(
        gradle_props
    ).items():

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

    except ValueError:

        error(
            'Config error: '
            '"versionCode" must be an integer'
        )

    config["outdir"] = Path(
        config["outdir"]
    )

    config["outdir"].mkdir(
        mode=0o755,
        parents=True,
        exist_ok=True,
    )

    if "abiList" in config:

        abis = set(
            re.split(
                r"\s*,\s*",
                config["abiList"],
            )
        )

    else:

        abis = default_abis

    set_build_abis(abis)


# ============================================================
# ARGUMENTS
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description="Magisk build script"
    )

    parser.set_defaults(
        func=lambda: None
    )

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
        help="verbose output",
    )

    parser.add_argument(
        "-c",
        "--config",
        default="config.prop",
        help="custom config file",
    )

    subparsers = (
        parser.add_subparsers(
            title="actions"
        )
    )

    all_parser = subparsers.add_parser(
        "all",
        help="build everything",
    )

    native_parser = subparsers.add_parser(
        "native",
        help="build native binaries",
    )

    native_parser.add_argument(
        "targets",
        nargs="*",
        help=(
            "targets: "
            + ", ".join(support_targets)
        ),
    )

    app_parser = subparsers.add_parser(
        "app",
        help="build the Magisk app",
    )

    app_ng_parser = subparsers.add_parser(
        "app-ng",
        help="build next generation app",
    )

    stub_parser = subparsers.add_parser(
        "stub",
        help="build stub app",
    )

    test_parser = subparsers.add_parser(
        "test",
        help="build test app",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="cleanup",
    )

    clean_parser.add_argument(
        "targets",
        nargs="*",
        help="native, cpp, rust, app",
    )

    ndk_parser = subparsers.add_parser(
        "ndk",
        help="setup Magisk NDK",
    )

    emu_parser = subparsers.add_parser(
        "emulator",
        help="setup AVD",
    )

    emu_parser.add_argument(
        "apk",
        nargs="?",
        help="Magisk APK",
    )

    emu_parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="build before patching",
    )

    avd_patch_parser = subparsers.add_parser(
        "avd_patch",
        help="patch AVD image",
    )

    avd_patch_parser.add_argument(
        "image"
    )

    avd_patch_parser.add_argument(
        "output"
    )

    avd_patch_parser.add_argument(
        "--apk"
    )

    avd_patch_parser.add_argument(
        "-b",
        "--build",
        action="store_true",
    )

    cargo_parser = subparsers.add_parser(
        "cargo",
        help="run cargo",
    )

    cargo_parser.add_argument(
        "commands",
        nargs=argparse.REMAINDER,
    )

    clippy_parser = subparsers.add_parser(
        "clippy",
        help="run clippy",
    )

    clippy_parser.add_argument(
        "--abi",
        action="append",
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

    rustup_parser = subparsers.add_parser(
        "rustup",
        help="setup rustup wrapper",
    )

    rustup_parser.add_argument(
        "wrapper_dir"
    )

    gen_parser = subparsers.add_parser(
        "gen",
        help="generate IDE files",
    )

    gen_parser.add_argument(
        "--abi"
    )

    # callbacks

    all_parser.set_defaults(
        func=build_all
    )

    native_parser.set_defaults(
        func=build_native
    )

    app_parser.set_defaults(
        func=build_app
    )

    app_ng_parser.set_defaults(
        func=build_app_ng
    )

    stub_parser.set_defaults(
        func=build_stub
    )

    test_parser.set_defaults(
        func=build_test
    )

    clean_parser.set_defaults(
        func=cleanup
    )

    ndk_parser.set_defaults(
        func=setup_ndk
    )

    emu_parser.set_defaults(
        func=setup_avd
    )

    avd_patch_parser.set_defaults(
        func=patch_avd_file
    )

    cargo_parser.set_defaults(
        func=cargo_cli
    )

    clippy_parser.set_defaults(
        func=clippy_cli
    )

    rustup_parser.set_defaults(
        func=setup_rustup
    )

    gen_parser.set_defaults(
        func=gen_ide
    )

    if len(sys.argv) == 1:

        parser.print_help()
        sys.exit(1)

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():

    global args

    args = parse_args()

    args.config = Path(
        args.config
    )

    load_config()

    args.func()


if __name__ == "__main__":
    main()
