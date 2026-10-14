# Personal shell scripts

Collection of my script utilities for display brightness, screenshots, picture management,
PDF compression, and backups, plus shared output and color helpers. These scripts
target Linux and use GNU command-line utilities; use Bash 4 or newer.

## Contents

| File | Purpose |
| --- | --- |
| [archivePictureDir](#archivepicturedir) | Move picture directories into a personal archive. |
| [openwrt_pullconfig](#openwrt_pullconfig) | Copy essential OpenWrt configuration over SSH into Git. |
| [openwrt_pushconfig](#openwrt_pushconfig) | Restore configuration and packages while preserving secrets. |
| [openwrt_secrets](#openwrt_secrets) | Check UCI secrets and install a pre-commit check. |
| [compress_pictures](#compress_pictures) | Resize and recompress JPEG images with ImageMagick. |
| [dar_wrapper](#dar_wrapper) | Create full and subsequent backups with DAR. |
| [ddcbrightness](#ddcbrightness) | Adjust external monitor backlight brightness over DDC/CI. |
| [flameshot_wrapper](#flameshot_wrapper) | Open Flameshot with a window or screen region selected. |
| [shrinkpdf](#shrinkpdf) | Reduce PDF size with qpdf, pdftk, and optionally Ghostscript. |
| [lib_output](#lib_output) | Shared status, diagnostic, and error functions. |
| [lib_colors](#lib_colors) | Terminal color and formatting variables for interactive Bash. |

## Setup

Run the executables directly from this checkout, for example `./ddcbrightness up`.
Install the dependencies listed below for the scripts you use.

`compress_pictures`, `dar_wrapper`, and `shrinkpdf` require `lib_output`, which
also loads `lib_colors`. From the repository directory, install the libraries:

```bash
sudo install -d /usr/local/lib
sudo install -m 644 lib_output lib_colors /usr/local/lib/
export PATH="$PWD:/usr/local/lib:$PATH"
```

The `PATH` entry for the repository makes the commands available from other
directories. The library entry lets `shrinkpdf` resolve its bare `source lib_output`
statement. Keep this checkout in place and add the equivalent absolute paths to
your shell configuration if you want the setup to persist.

For a user-only library installation, the loaders support a `BASH_PREFIX`:

```bash
export BASH_PREFIX="$HOME/.local/share/personal-scripts/"
mkdir -p "${BASH_PREFIX}usr/local/lib"
install -m 644 lib_output lib_colors "${BASH_PREFIX}usr/local/lib/"
export PATH="$PWD:${BASH_PREFIX}usr/local/lib:$PATH"
```

Keep the trailing slash in `BASH_PREFIX`; the scripts append `usr/local/lib` directly.

## Source versions

All scripts embed a source date and Git commit ID and print them with
`--version`, before checking dependencies or performing any work:

```bash
./openwrt_pushconfig --version
./ddcbrightness --version
bash lib_output --version
python3 tests/test_openwrt_config.py --version
```

This also works for standalone copies outside Git. Libraries only handle this
option when executed directly, so sourcing them preserves the caller's arguments.
The existing `-v` options still mean verbose.

Run `./update_versions` after committing source changes and before distributing
scripts. This Bash helper updates the marked version blocks in scripts and tests
using each file's latest commit date (`YYYY-MM-DD`) and abbreviated commit ID.
`+dirty` marks source edits relative to HEAD; new files use HEAD's date and ID
with `+uncommitted`. Changes inside the version block alone do not count as dirty.
Repeated runs without source changes produce identical files.

The ID identifies the committed source used when stamping, not the later commit
that stores the stamp itself: embedding a commit's own ID would change that ID.
The helper requires Git, Bash, and standard Unix utilities; normal version output
only requires the script's interpreter. Version stamps can be committed alongside
other changes, but must be refreshed to describe later source revisions.

## Scripts

### archivePictureDir

[Source](archivePictureDir) · Dependencies: GNU `readlink`, `mkdir`, `mv`, `rmdir`.

Moves the non-hidden contents of each supplied directory into
`/home/$USER/Pictures/0.ARCHIV`, then attempts to remove the empty source directory.
The archive base must already exist; change `archivebase` in the script to use a
different location.

```bash
./archivePictureDir /home/alice/Pictures/Holidays
```

The destination is derived by stripping the first three components of the absolute
source path. For user `alice`, the example moves files into
`/home/alice/Pictures/0.ARCHIV/Holidays`.

This moves files rather than copying them. Hidden entries remain in the source,
preventing its removal. The move command uses unquoted paths, so directory names
containing spaces are not supported reliably.

### openwrt_pullconfig

[Source](openwrt_pullconfig) · Dependencies: OpenSSH `ssh`, Git, GNU `tar`, `rsync`,
`mktemp`, `grep`, and GNU `realpath` locally; SSH access, UCI, `awk`, and `tar`
on the router. Install the companion `openwrt_secrets` beside both scripts
(with the same optional executable prefix, e.g. `mo-openwrt_secrets`).
The script is Bash; Python is not required. UCI is needed only on the router.

Copies `/etc/config`, containing the main network, Wi-Fi, firewall, DHCP, and
system settings, into an existing local Git working tree. This is a configuration
snapshot, not a complete system backup. When present, `/etc/apk` is also mirrored
into `NAME/etc/apk`, including `world`, repository definitions, signing keys, and
other APK settings. `world` records explicitly requested packages and constraints;
APK resolves their dependencies during restore. SSH keys, custom scripts outside
these directories, and package binaries are not included. Packages installed from
local APK files must also be made available separately for restoration.

```bash
./openwrt_pullconfig root@192.168.1.1 /path/to/router-config-repo
./openwrt_pullconfig --name upstairs upstairs-router /path/to/router-config-repo
```

The default destination is `openwrt/etc/config` inside the repository root;
`--name upstairs` uses `upstairs/etc/config`. Use a different name for each router.
For a custom SSH port or identity, define an alias in `~/.ssh/config` and pass it
as the host. The remote account must be able to read the configuration files.

Both pull and push compare the router's current hostname (from
`/proc/sys/kernel/hostname`) with `NAME`, the snapshot directory name. An exact
match is required; SSH aliases and DNS names such as `corewrt.mgmt` do not affect
this comparison. The check runs in the transfer/installation SSH session before
changing local or remote files. A mismatch aborts with the expected and actual
hostname. Use `--force` to bypass this check explicitly:

```bash
./openwrt_pullconfig --force --name corewrt root@otherwrt /path/to/router-config-repo
./openwrt_pushconfig --force --name corewrt root@otherwrt /path/to/router-config-repo
```

`--force` only skips the hostname check; configuration and archive validation
still apply. No identity file or initial binding is needed.

Each run mirrors the router's files, overwriting local edits and removing files
that no longer exist remotely in that destination. Downloads and extraction
finish before the destination is updated. The script displays Git status but
does not stage, commit, or push. Review and commit the snapshot yourself:

```bash
git -C /path/to/router-config-repo diff -- openwrt/etc openwrt/secret-paths openwrt/.gitignore
git -C /path/to/router-config-repo add -- openwrt/etc openwrt/secret-paths openwrt/.gitignore
git -C /path/to/router-config-repo commit -m "Update OpenWrt configuration"
```

Known UCI secrets are removed in a temporary directory on the router, before
any configuration is transferred. The running configuration is not changed.
`NAME/secret-paths` records the removed option paths and section types without
values; commit it together with `NAME/.gitignore` and the public configuration.
Pull adds `/secrets/` to `NAME/.gitignore` and refuses already tracked secret
files. Existing local overrides under `NAME/secrets/` are never overwritten.
Packages containing secrets are exported through UCI and lose their original
formatting/comments. Other configuration files retain their original bytes.
The local copy is restricted to the current user. Only regular UCI package files
with alphanumeric/underscore filenames are accepted in `/etc/config`. APK files
may use nested directories and dots, plus signs, and hyphens in filenames. Links
and special files are rejected in both snapshots.

### openwrt_pushconfig

[Source](openwrt_pushconfig) · Dependencies: Bash, OpenSSH `ssh`, Git, GNU `tar`,
`mktemp`, GNU `realpath`, `find`, `chmod`, and `grep` locally; root SSH access,
UCI, `awk`, `ubus`, `tar`,
and standard shell utilities on the router. Neither Python nor `nohup` is needed.

```bash
./openwrt_pushconfig root@192.168.1.1 /path/to/router-config-repo
./openwrt_pushconfig --name corewrt root@10.10.20.1 /path/to/router-config-repo
```

Checks the router hostname against `NAME` before installing anything; `--force`
skips this check, as described above. Reads the same `NAME/etc/config` and optional `NAME/etc/apk` directories as
`openwrt_pullconfig`. Edit the local
files, then push to install and apply them. Known secret options in public files
are rejected. Missing secret options are copied from the existing matching
router sections. Explicit overrides from ignored `NAME/secrets/PACKAGE` UCI
files take precedence. Overrides may contain only protected options, in sections
already defined in the public files with the same name and type. Lists are
replaced, not appended; quoting and multiline values are retained. Empty secret
overrides are rejected because UCI interprets empty string options as removal.
Sections containing secrets must be named. Anonymous secret-bearing sections
are refused, so a section reorder cannot attach passwords to another section.

Example public file `corewrt/etc/config/wireless`:

```uci
config wifi-iface 'guest'
    option device 'radio0'
    option network 'guest'
    option ssid 'Guest'
    option encryption 'psk2'
```

The private override `corewrt/secrets/wireless` supplies a new password:

```uci
config wifi-iface 'guest'
    option key 'your-new-password'
```

Create `corewrt/secrets` with mode 700 and its files with mode 600. Push also
restricts their permissions, checks that Git ignores the directory, and refuses
tracked secret files. If you create a snapshot locally without pulling first,
add `/secrets/` to `corewrt/.gitignore` yourself. Inline passwords in newly created
public files must be moved into these overrides before pushing or committing.

Before any live changes, push checks that all options recorded in `secret-paths`
exist in the merged configuration for retained sections. A missing value on a
new router must be supplied through an override. Removed sections/packages do
not require their old secrets. New secured Wi-Fi (PSK/SAE/WEP) and WireGuard
interfaces are also checked for their required key. For other new services,
add required paths and section types to `secret-paths` explicitly, e.g.
`network.wan.password interface`. UCI parsing alone does not validate a service's
complete credential requirements. `--force` never skips secret validation.

When an APK snapshot is present, the router must have `apk`. Before applying UCI
files, push backs up `/etc/apk` alongside the UCI backup, restores the saved APK
settings, runs `apk update`, and runs
`apk add --` without package arguments against the restored `world`. This installs
missing requirements and removes packages no longer requested or needed as
dependencies. Already installed packages satisfying the saved constraints are
retained; no upgrade or reinstall is requested. Removing a package from `world`
therefore removes it only if no remaining package depends on it. An unchanged
package selection causes no package installation or removal. A configuration-only
snapshot still works on routers without APK. Repository URLs must match the
router's firmware, architecture, and kernel; requested packages must remain
available from those repositories. APK may adjust dependencies to satisfy the
saved constraints. See the [APK add documentation](https://github.com/alpinelinux/apk-tools/blob/master/doc/apk-add.8.scd).

If repository updates or installation fail, push reports failure, restores the
previous APK settings, and does not apply the uploaded UCI files or schedule
reloads. Package changes already performed by APK, including effects of package
scripts, cannot be automatically rolled back. The backup remains on the router.

Snapshots created by the previous scripts may still contain `[REDACTED]`.
Push rejects these files. Save local edits separately, pull a fresh sanitized
snapshot, and put any new passwords in private overrides before pushing.
Pull overwrites local edits.

The router validates every uploaded package with UCI and refuses pending UCI
changes before installation. Files absent locally remain on the router; options
and sections removed from uploaded files are removed. A private backup of
`/etc/config` is created under `/root/openwrt-config-backup.XXXXXX/config` before
installation. An installation failure attempts to restore the affected files.

After writing each file, the script compares it with the upload. Only then does
it report `Configuration installed and verified`. The initial `Uploading ...`
message announces the transfer attempt and does not confirm installation.

The script schedules a `config.change` service event through `ubus` for each
uploaded package after a short delay. A background shell ignores hangup signals
and redirects its input/output, so it needs no `nohup` executable. Services must
handle these events to reload automatically. Network or Wi-Fi changes may
disconnect clients or change the router's address. The printed backup directory
contains `apply.log` with notification results; successful upload does not verify
service health or connectivity, and there is no automatic rollback after a reload.

### openwrt_secrets

[Source](openwrt_secrets) · Dependencies: Bash, Git, `awk`, and standard Unix tools.
No local UCI is needed. Pull and push use this companion to send UCI filtering
and merge functions to the router.

The explicit default protected option names are:
`password`, `passwd`, `key`, `key1`, `key2`, `key3`, `key4`, `secret`, `token`, `psk`, `private_key`,
`preshared_key`, `client_secret`, `auth_secret`, `radius_secret`, `acct_secret`,
`auth_key`, `acct_key`, `sae_password`, `wpa_psk`, `shared_secret`, `api_key`,
`access_token`, `refresh_token`, `tls_auth`, and `tls_crypt`.
Add extra exact option names (one per line, comments allowed) to
`NAME/secret-options` and commit that file. These names apply across UCI packages;
unknown option names are not automatically classified. This covers UCI options,
not secrets in APK repository URLs, unrelated files, or arbitrary comments.

Check the working tree or install the commit guard in your configuration repo:

```bash
./openwrt_secrets --check /path/to/router-config-repo
./openwrt_secrets --install-hook /path/to/router-config-repo
```

The installed hook checks all public configuration files in the Git index,
including multiline and quoted options, and blocks tracked/staged files under
`NAME/secrets/`, even if added with `git add -f`. It reports paths, never values.
The staged policy and working policy are both used. Hook installation refuses
to overwrite an existing `pre-commit` hook; call
`openwrt_secrets --check-staged .` from that hook instead. Install the hook once
per clone; to update an installed check, copy the new helper over
`$(git rev-parse --git-path hooks)/openwrt-secrets-check`. Hooks are not transferred
by Git. Git hooks can be bypassed deliberately, so they are an accident guard.
Secrets already committed remain in the repository history; remove/rotate them
separately if needed.

Run `UCI_TEST_BINARY=/path/to/uci python3 tests/test_openwrt_config.py` to test
filtering and merging with a real UCI CLI in the simulated router. Without it,
the UCI integration cases are skipped; the Git guard and transfer tests still run.

### compress_pictures

[Source](compress_pictures) · Dependencies: ImageMagick's `identify` and `convert`,
`file`, and the shared libraries.

Processes JPEG files, optionally adjusting quality and fitting images inside a
square bounding box while preserving their aspect ratio.

```bash
# Keep originals and write resized images into compress_pictures.<PID>/
./compress_pictures --keep --resize 1920 --quality 85 photo.jpg

# Resize matching JPEGs in place
./compress_pictures --resize 1920 ./*.[jJ][pP][gG]
```

- `-r`, `--resize N`: fit within `N × N` pixels without enlarging smaller images.
- `-q`, `--quality N`: pass a quality percentage to ImageMagick.
- `-k`, `--keep`: leave originals untouched and retain output in the temporary directory.
- `-f`, `--force`: run conversion even when an image already fits the requested dimensions.
- `-v`, `--verbose`, `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

With `--resize`, converted files replace originals unless `--keep` is set. Without
`--resize`, output remains in `compress_pictures.<PID>/` even without `--keep`.
Despite the built-in help text, the implementation does not compare output file
sizes before replacing originals. Non-JPEG files are skipped. Shell globs expand
before the script runs, so the example explicitly matches both `.jpg` and `.JPG`.

### dar_wrapper

[Source](dar_wrapper) · Dependencies: `dar`, `renice`, and the shared libraries.

Creates timestamped DAR backups and separate catalogs in an existing destination
directory. Archive names derive from the source path: `/home/alice` becomes
`home_alice`, and `/` becomes `rootfs`.

```bash
./dar_wrapper /home/alice /mnt/backup
./dar_wrapper --full /home/alice /mnt/backup
./dar_wrapper --exclude excluded-dirs.txt /home/alice /mnt/backup
./dar_wrapper --info /home/alice /mnt/backup
```

The first run creates a full backup. Later runs use the `.last.cat.1.dar` catalog
as their reference and update that link to the new catalog. The script calls these
backups “differential,” but each references the latest backup, forming an incremental
chain. Retain earlier archives needed for restoration.

- `-f`, `--full`: force a full backup.
- `-e`, `--exclude FILE`: read directory exclusion patterns, one per line.
- `-i`, `--info`: print configuration without creating a backup; source and target are still required.
- `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

Defaults use bzip2 compression, stay on the source filesystem, exclude `*.dar` and
`*.swp`, and avoid recompressing common compressed formats. Adjust the configuration
block near the top for other defaults. Encryption is marked unsupported in the
script's help. Some generated commands do not quote paths, so use paths without spaces.

### ddcbrightness

[Source](ddcbrightness) · Dependencies: `ddcutil`, monitors supporting DDC/CI, and
permission to access them. `notify-send` is optional for desktop notifications.

Changes the physical backlight brightness of external monitors, useful alongside
the desktop's built-in controls for a laptop screen. By default, it detects all
DDC displays, changes brightness by 10, and clamps the result to 0–100.

```bash
./ddcbrightness up
./ddcbrightness --step 5 down
./ddcbrightness --display 1 up
```

- `-s`, `--step N`: set the step size; the intended range is 1–50.
- `-i`, `--display N`: target a particular `ddcutil` display number.
- `-d`, `--debug`, and `-h`, `--help`: diagnostics and usage.

Bind `ddcbrightness down` and `ddcbrightness up` to desktop shortcuts such as
`Super+Alt+F3` and `Super+Alt+F4` to control external displays from the keyboard.

### flameshot_wrapper

[Source](flameshot_wrapper) · Dependencies: `flameshot`, `xdotool`, and an X11 session.

Opens the Flameshot screenshot GUI with a region derived from window geometry.

```bash
./flameshot_wrapper activewindow   # Use the active window's bounds
./flameshot_wrapper selectwindow   # Click a window to use its bounds
./flameshot_wrapper                # Pass screen<N> as the region
```

With no argument (or an unrecognized argument), `N` comes from
`xdotool get_desktop`. This is the virtual desktop index, so it may not identify
the intended physical monitor on a multi-monitor setup. The wrapper has no help flag.

### shrinkpdf

[Source](shrinkpdf) · Dependencies: `qpdf`, `pdftk`, Ghostscript (`gs`), `bc`, `file`,
GNU `du`, `awk`, and the shared libraries. `gs` must be installed even when no
Ghostscript preset is selected.

Runs PDFs through qpdf and pdftk, compares the resulting sizes, and normally replaces
the original only when a smaller result is available. Files must have a `.pdf` or
`.PDF` extension and be recognized as PDFs by `file`. Arguments containing
`noshrink` are skipped.

```bash
./shrinkpdf document.pdf
./shrinkpdf --keep --pdfsetting ebook scan.pdf
./shrinkpdf --verbose ./*.pdf
```

- `-k`, `--keep`: rename the original to `<file>_orig` before choosing the output.
- `-p`, `--pdfsetting PRESET`: run Ghostscript first, using `default`, `prepress`,
  `printer`, `ebook`, or `screen`. This can reduce image quality.
- `-v`, `--verbose`, and `-h`, `--help`: diagnostics and usage.

With `--keep`, if neither compressed candidate is smaller, the original remains
under `<file>_orig`; it is not restored to its original filename. Ghostscript
intermediate files are also retained with this option.

## Shared libraries

### lib_output

[Source](lib_output) · Dependency: `lib_colors` in the configured library directory.

Source this file from Bash to use `success`, `warn`, `error`, `debug`, and their
`_n` variants, which omit the trailing newline. Messages go to standard error;
debug output is enabled by a non-empty `opt_debug`. `die` reports an error and exits
with the preceding command's status, so it does not always return a nonzero code.

```bash
source /usr/local/lib/lib_output
success "Finished"
opt_debug=1
debug "Extra detail"
```

### lib_colors

[Source](lib_colors) · Dependency: `tput` and a suitable terminal definition.

Source this file in interactive Bash to export terminal formatting variables such
as `COLOR_RED`, `COLOR_GREEN`, `COLOR_BOLD`, and `COLOR_NONE` (reset). It prints a
loading message in interactive shells and returns without initializing colors in
non-interactive shells.

```bash
source /usr/local/lib/lib_colors
printf '%sHello%s\n' "$COLOR_GREEN" "$COLOR_NONE"
```
