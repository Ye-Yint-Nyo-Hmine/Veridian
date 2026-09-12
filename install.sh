#!/bin/sh
# Veridian installer — Linux and macOS.
#
#   curl -LsSf https://raw.githubusercontent.com/Ye-Yint-Nyo-Hmine/Veridian/main/install.sh | sh
#
# Installs a versioned copy of Veridian under $VERIDIAN_HOME (default ~/.veridian), gives it its
# own virtual environment, and puts a `veridian` launcher on PATH. Re-running is the upgrade path:
# the new version is staged and smoke-tested in full before the one-line `current` pointer is
# flipped, so a failed upgrade leaves the working install untouched.
#
# Options:
#   --version <v>      install a specific version (default: the latest GitHub release)
#   --tarball <path>   install from a local tarball instead of downloading (used by the tests)
#   --no-modify-path   install the launcher but do not touch any shell profile
#   --force            reinstall even if this version is already the active one
#   --help

set -eu

REPO="Ye-Yint-Nyo-Hmine/Veridian"
PYTHON_VERSION="3.13"
MARKER="# added by the veridian installer"

VERSION="${VERIDIAN_VERSION:-latest}"
TARBALL="${VERIDIAN_TARBALL:-}"
MODIFY_PATH=1
FORCE=0

say() { printf '%s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die() { printf 'veridian: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
        --version=*) VERSION="${1#*=}"; shift ;;
        --tarball) [ $# -ge 2 ] || die "--tarball needs a path"; TARBALL="$2"; shift 2 ;;
        --tarball=*) TARBALL="${1#*=}"; shift ;;
        --no-modify-path) MODIFY_PATH=0; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

VERIDIAN_HOME="${VERIDIAN_HOME:-$HOME/.veridian}"

# ---------------------------------------------------------------- downloading

if need curl; then
    fetch() { curl -fsSL "$1" -o "$2"; }
    fetch_stdout() { curl -fsSL "$1"; }
elif need wget; then
    fetch() { wget -qO "$2" "$1"; }
    fetch_stdout() { wget -qO- "$1"; }
else
    fetch() { die "need curl or wget to download"; }
    fetch_stdout() { die "need curl or wget to download"; }
fi

# uv writes console scripts to bin/veridian on Unix and Scripts/veridian.exe on Windows; Git Bash
# and MSYS run this script against the latter.
venv_veridian() {
    for candidate in "$1/bin/veridian" "$1/Scripts/veridian.exe"; do
        [ -x "$candidate" ] && printf '%s' "$candidate" && return 0
    done
    return 1
}

sha256_of() {
    if need sha256sum; then sha256sum "$1" | awk '{print $1}'
    elif need shasum; then shasum -a 256 "$1" | awk '{print $1}'
    elif need openssl; then openssl dgst -sha256 "$1" | awk '{print $NF}'
    else die "need sha256sum, shasum, or openssl to verify the download"
    fi
}

# ------------------------------------------------------------------ uv

ensure_uv() {
    if need uv; then return; fi
    say "uv not found — installing it (Veridian uses it to manage interpreters and brick environments)"
    fetch_stdout "https://astral.sh/uv/install.sh" | sh >/dev/null 2>&1 \
        || die "could not install uv; install it from https://docs.astral.sh/uv/ and re-run"
    # uv lands in one of these; make it visible to this process before we rely on it.
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "${XDG_BIN_HOME:-}"; do
        [ -n "$d" ] && [ -x "$d/uv" ] && PATH="$d:$PATH" && export PATH
    done
    need uv || die "uv installed but is not on PATH; open a new shell and re-run"
}

# ------------------------------------------------------------- resolve version

resolve_version() {
    if [ "$VERSION" != "latest" ]; then
        printf '%s' "${VERSION#v}"
        return
    fi
    tag=$(fetch_stdout "https://api.github.com/repos/$REPO/releases/latest" \
        | grep -m1 '"tag_name"' \
        | sed -e 's/.*"tag_name"[[:space:]]*:[[:space:]]*"//' -e 's/".*//') || true
    [ -n "${tag:-}" ] || die "could not determine the latest release of $REPO (try --version <v>)"
    printf '%s' "${tag#v}"
}

# ------------------------------------------------------------------- install

ensure_uv

TMP=$(mktemp -d "${TMPDIR:-/tmp}/veridian-install.XXXXXX")
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

if [ -n "$TARBALL" ]; then
    [ -f "$TARBALL" ] || die "no such tarball: $TARBALL"
    say "Installing Veridian from $TARBALL"
    cp "$TARBALL" "$TMP/veridian.tar.gz"
    # A local tarball is trusted as given; its version comes from the tree it unpacks to.
    VER="local"
else
    VER=$(resolve_version)
    say "Installing Veridian $VER"
    name="veridian-$VER.tar.gz"
    base="https://github.com/$REPO/releases/download/v$VER"
    info "downloading $name"
    fetch "$base/$name" "$TMP/veridian.tar.gz" || die "could not download $base/$name"
    fetch "$base/SHA256SUMS" "$TMP/SHA256SUMS" || die "could not download $base/SHA256SUMS"

    expected=$(grep " \*\{0,1\}$name\$" "$TMP/SHA256SUMS" | awk '{print $1}' | head -n1)
    [ -n "$expected" ] || die "SHA256SUMS has no entry for $name"
    actual=$(sha256_of "$TMP/veridian.tar.gz")
    [ "$expected" = "$actual" ] || die "checksum mismatch for $name (expected $expected, got $actual)"
    info "checksum verified"
fi

mkdir -p "$TMP/app"
tar -xzf "$TMP/veridian.tar.gz" -C "$TMP/app" --strip-components=1 \
    || die "could not extract the archive"
[ -f "$TMP/app/pyproject.toml" ] || die "archive does not look like a Veridian tree"

# A local tarball does not name its version; read it out of the tree we just unpacked.
if [ "$VER" = "local" ]; then
    VER=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$TMP/app/src/veridian/__init__.py" | head -n1)
    [ -n "$VER" ] || die "could not read the version out of the archive"
    info "version $VER"
fi

TARGET="$VERIDIAN_HOME/versions/$VER"
if [ -d "$TARGET" ] && [ "$FORCE" -eq 0 ] && [ "$(cat "$VERIDIAN_HOME/current" 2>/dev/null || true)" = "$VER" ]; then
    say "Veridian $VER is already installed (use --force to reinstall)"
    exit 0
fi

# Everything below is built at its final path. A virtual environment records absolute paths and
# does not survive being moved, so it cannot be assembled in the temp directory and relocated.
# Building a *new* version directory still leaves the running install alone: `current` keeps
# pointing at the old version until the very last step.
#
# The exception is --force over the version that is currently live, which has to overwrite it. If
# that fails, drop the pointer too: "no installation found" is a far better state to land in than
# a pointer naming a directory that is now half-built.
REBUILDING_ACTIVE=0
[ "$(cat "$VERIDIAN_HOME/current" 2>/dev/null || true)" = "$VER" ] && REBUILDING_ACTIVE=1

abandon() {
    rm -rf "$TARGET"
    if [ "$REBUILDING_ACTIVE" -eq 1 ]; then
        rm -f "$VERIDIAN_HOME/current"
        die "$1 — and the previous $VER install was replaced by this attempt, so re-run to restore it"
    fi
    die "$1"
}

mkdir -p "$VERIDIAN_HOME/versions"
rm -rf "$TARGET"
mkdir -p "$TARGET"
mv "$TMP/app" "$TARGET/app" || abandon "could not move the extracted tree into place"

info "creating the environment (python $PYTHON_VERSION)"
uv venv --python "$PYTHON_VERSION" --quiet "$TARGET/venv" \
    || abandon "could not create a Python $PYTHON_VERSION environment"

info "installing dependencies"
# Installed from inside the tree as ".[providers]": an absolute "<path>[extras]" argument is not a
# plain path, so environments that rewrite paths for a native uv (MSYS, Git Bash) mangle it.
(cd "$TARGET/app" && uv pip install --quiet --python "$TARGET/venv" ".[providers]") \
    || abandon "could not install Veridian into its environment"

# Smoke-test before the pointer moves, so a broken build never becomes the active one.
staged=$(venv_veridian "$TARGET/venv") || abandon "the install produced no veridian launcher"
"$staged" --version >/dev/null 2>&1 \
    || abandon "the freshly installed veridian failed to start; leaving your current install alone"

# The pointer file is written last and atomically: it is what makes this version the live one.
printf '%s\n' "$VER" > "$VERIDIAN_HOME/current.tmp"
mv "$VERIDIAN_HOME/current.tmp" "$VERIDIAN_HOME/current"

# --------------------------------------------------------------------- launcher

BIN_DIR="$VERIDIAN_HOME/bin"
NEEDS_PATH=1
case ":$PATH:" in
    *":$BIN_DIR:"*) NEEDS_PATH=0 ;;
esac

# For a default install, ~/.local/bin already on PATH is the better home for the launcher: the
# command works in this shell straight away, with no profile edit and no restart. A custom
# VERIDIAN_HOME stays entirely self-contained instead, so a throwaway install (a test, a CI job,
# a second version side by side) never writes into a directory the user's PATH points at.
if [ "$NEEDS_PATH" -eq 1 ] && [ "$MODIFY_PATH" -eq 1 ] && [ "$VERIDIAN_HOME" = "$HOME/.veridian" ]; then
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) BIN_DIR="$HOME/.local/bin"; NEEDS_PATH=0 ;;
    esac
fi

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/veridian" <<EOF
#!/bin/sh
# Veridian launcher. Resolves the active version at run time, so an upgrade never has to
# rewrite this file.
VERIDIAN_HOME="\${VERIDIAN_HOME:-$VERIDIAN_HOME}"
version=\$(cat "\$VERIDIAN_HOME/current" 2>/dev/null) || {
    echo "veridian: no installation found under \$VERIDIAN_HOME" >&2
    exit 1
}
venv="\$VERIDIAN_HOME/versions/\$version/venv"
[ -x "\$venv/bin/veridian" ] && exec "\$venv/bin/veridian" "\$@"
exec "\$venv/Scripts/veridian.exe" "\$@"
EOF
chmod 755 "$BIN_DIR/veridian"

# ------------------------------------------------------------------------ PATH

added_to=""
add_line_to() {
    file="$1"; line="$2"
    [ -f "$file" ] || return 0
    grep -qF "$MARKER" "$file" 2>/dev/null && return 0
    printf '\n%s\n%s\n' "$MARKER" "$line" >> "$file"
    added_to="$added_to $file"
}

if [ "$MODIFY_PATH" -eq 1 ] && [ "$NEEDS_PATH" -eq 1 ]; then
    export_line="export PATH=\"$BIN_DIR:\$PATH\""
    add_line_to "$HOME/.bashrc" "$export_line"
    add_line_to "$HOME/.bash_profile" "$export_line"
    add_line_to "$HOME/.profile" "$export_line"
    add_line_to "${ZDOTDIR:-$HOME}/.zshrc" "$export_line"
    if [ -d "$HOME/.config/fish" ]; then
        mkdir -p "$HOME/.config/fish/conf.d"
        fish_file="$HOME/.config/fish/conf.d/veridian.fish"
        if [ ! -f "$fish_file" ]; then
            printf '%s\nset -gx PATH "%s" $PATH\n' "$MARKER" "$BIN_DIR" > "$fish_file"
            added_to="$added_to $fish_file"
        fi
    fi
fi

# ----------------------------------------------------------------------- done

say ""
say "Veridian $VER installed."
info "root      $TARGET/app"
info "launcher  $BIN_DIR/veridian"
say ""
if [ "$NEEDS_PATH" -eq 0 ]; then
    say "Run 'veridian doctor' to check your setup, then 'veridian' in any project directory."
elif [ -n "$added_to" ]; then
    say "Added $BIN_DIR to your PATH in:$added_to"
    say "Restart your shell (or 'export PATH=\"$BIN_DIR:\$PATH\"'), then run 'veridian doctor'."
else
    say "Add this to your shell profile, then run 'veridian doctor':"
    info "export PATH=\"$BIN_DIR:\$PATH\""
fi
say ""
say "Veridian treats the directory you run it from as its workspace."
