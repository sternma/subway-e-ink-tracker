#!/usr/bin/env bash
#
# Provision a Raspberry Pi 5 running Raspberry Pi OS Lite (64-bit) to drive the
# transit display on a Raspberry Pi Touch Display 2.
#
# Idempotent: safe to re-run. Reports whether a reboot is needed at the end.
#
#   curl -fsSL .../provision_pi.sh | bash        # or just: bash provision_pi.sh
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sternma/subway-e-ink-tracker.git}"
REPO_DIR="${REPO_DIR:-$HOME/subway-e-ink-tracker}"
BOOT_CONFIG=/boot/firmware/config.txt
BOOT_CMDLINE=/boot/firmware/cmdline.txt
PANEL_OVERLAY="dtoverlay=vc4-kms-dsi-ili79600-10-1inch"
UDEV_RULE=/etc/udev/rules.d/99-backlight-video.rules

reboot_needed=0

log()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

require_sudo() {
  if ! sudo -n true 2>/dev/null; then
    echo "This script needs sudo. Run 'sudo -v' first, then re-run." >&2
    exit 1
  fi
}

backup_once() {
  local file=$1
  [ -f "${file}.orig" ] || sudo cp -a "$file" "${file}.orig"
}

# --- packages ---------------------------------------------------------------
# libcairo2 is the shared library cairocffi/cairosvg load at runtime to
# rasterize the weather and UI icons; Pi OS Lite does not ship it.
install_packages() {
  log "Installing system packages"
  local missing=()
  for pkg in git libcairo2 i2c-tools; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if [ ${#missing[@]} -eq 0 ]; then
    note "already present: git libcairo2 i2c-tools"
    return
  fi
  note "installing: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${missing[@]}"
}

# --- uv ---------------------------------------------------------------------
install_uv() {
  log "Installing uv"
  if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
    note "already installed"
  else
    curl -fsSL https://astral.sh/uv/install.sh | sh
  fi
  export PATH="$HOME/.local/bin:$PATH"
  note "$(uv --version)"
}

# --- panel overlay ----------------------------------------------------------
configure_panel() {
  log "Configuring the DSI panel overlay"
  if grep -q "vc4-kms-dsi-ili9881" "$BOOT_CONFIG"; then
    note "overlay already configured"
  else
    backup_once "$BOOT_CONFIG"
    sudo tee -a "$BOOT_CONFIG" >/dev/null <<EOF

# Raspberry Pi Touch Display 2 10-inch (1200x1920 DSI, ILI79600 panel)
$PANEL_OVERLAY
EOF
    note "appended $PANEL_OVERLAY"
    reboot_needed=1
  fi
}

# --- console hygiene --------------------------------------------------------
# Without these the console cursor blinks on top of the rendered frame and the
# kernel blanks the screen after ~10 minutes of no console output.
configure_console() {
  log "Quieting the text console"
  backup_once "$BOOT_CMDLINE"
  local added=()
  for param in consoleblank=0 vt.global_cursor_default=0 logo.nologo; do
    if ! grep -qw -- "$param" "$BOOT_CMDLINE"; then
      added+=("$param")
    fi
  done
  if [ ${#added[@]} -eq 0 ]; then
    note "already configured"
    return
  fi
  # cmdline.txt must stay a single line.
  sudo sed -i "1s|\$| ${added[*]}|" "$BOOT_CMDLINE"
  note "added: ${added[*]}"
  reboot_needed=1
}

# --- permissions ------------------------------------------------------------
configure_permissions() {
  log "Granting the video group access to the backlight"
  if [ ! -f "$UDEV_RULE" ]; then
    sudo tee "$UDEV_RULE" >/dev/null <<'EOF'
# Let members of the video group set panel brightness and power without root.
SUBSYSTEM=="backlight", RUN+="/bin/chgrp -R video /sys%p", RUN+="/bin/chmod -R g=u /sys%p"
EOF
    sudo udevadm control --reload-rules
    note "installed $UDEV_RULE"
  else
    note "udev rule already present"
  fi

  # The rule only fires on device add, so fix up the current boot too.
  for dev in /sys/class/backlight/*/; do
    [ -d "$dev" ] || continue
    sudo chgrp -R video "$dev" 2>/dev/null || true
    sudo chmod -R g=u "$dev" 2>/dev/null || true
    note "$(basename "$dev") is now group-writable"
  done

  for group in video input render; do
    if id -nG "$USER" | grep -qw "$group"; then
      note "$USER already in $group"
    else
      sudo usermod -aG "$group" "$USER"
      note "added $USER to $group (re-login required)"
    fi
  done
}

# --- application ------------------------------------------------------------
install_app() {
  log "Installing the application"
  if [ -d "$REPO_DIR/.git" ]; then
    note "repo present, pulling"
    git -C "$REPO_DIR" pull --ff-only
  else
    # Bird illustrations live in Git LFS and the bird screens are unregistered,
    # so skip the LFS payload entirely. It saves a lot of disk on a 1GB Pi.
    GIT_LFS_SKIP_SMUDGE=1 git clone "$REPO_URL" "$REPO_DIR"
  fi
  cd "$REPO_DIR"
  uv sync
  if [ ! -f config/.env ]; then
    cp config/.env.template config/.env
    note "created config/.env from the template -- edit it before running"
  else
    note "config/.env already exists, leaving it alone"
  fi
}

main() {
  require_sudo
  install_packages
  install_uv
  configure_panel
  configure_console
  configure_permissions
  install_app

  log "Done"
  if [ "$reboot_needed" -eq 1 ]; then
    note "REBOOT REQUIRED for the boot-config changes to take effect."
  else
    note "No reboot needed."
  fi
  note "Next: edit $REPO_DIR/config/.env, then"
  note "  cd $REPO_DIR && uv run runner.py"
  note "To install the service: sudo bash scripts/install_service.sh"
}

main "$@"
