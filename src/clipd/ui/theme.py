from __future__ import annotations

# clipd UI palette: Tokyo Night ink, cool ivory, lavender accent.
# Flat surfaces, sharp corners, compact developer density.
CSS = r"""
window.clipd-window {
  background-color: transparent;
}
.clipd-outer {
  padding: 12px;
}
.clipd-card {
  background-color: rgba(22, 22, 30, 0.994);
  border: 1px solid #2f334d;
  border-radius: 0;
  box-shadow: inset 0 1px 0 rgba(187, 154, 247, 0.22),
              0 12px 34px rgba(0, 0, 0, 0.58);
}
.topbar {
  padding: 15px 17px 6px 17px;
}
.brand {
  color: #bb9af7;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.8px;
  padding-right: 2px;
}
.brand-dot {
  background-color: #bb9af7;
  border-radius: 0;
  min-width: 7px;
  min-height: 7px;
}
.brand-sep {
  background-color: #2f334d;
  min-width: 1px;
  margin: 2px 10px 2px 4px;
}
.clipd-search {
  background: transparent;
  border: none;
  border-radius: 0;
  box-shadow: none;
  padding: 0;
  min-height: 30px;
  font-family: "Adwaita Sans", "Cantarell", sans-serif;
  font-size: 17px;
  color: #c0caf5;
}
.clipd-search:focus,
.clipd-search:focus-within,
.clipd-search entry:focus,
.clipd-search text:focus {
  outline: none;
  box-shadow: none;
  border: none;
  background: transparent;
}
.clipd-search image {
  color: #737aa2;
}
.clipd-search text {
  background: transparent;
  color: #c0caf5;
}
.clipd-search placeholder {
  color: #565f89;
}
selection {
  background-color: #4a3f70;
  color: #ffffff;
}
.count {
  color: #737aa2;
  font-size: 11px;
  padding-left: 10px;
}
.paused-badge {
  color: #c8b6ff;
  border: 1px solid #4c3a6e;
  background-color: #241f35;
  border-radius: 0;
  padding: 1px 8px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 1px;
  margin-left: 8px;
}
.gear {
  min-width: 28px;
  min-height: 28px;
  padding: 0;
  margin-left: 8px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0;
  color: #737aa2;
}
.gear:hover {
  background-color: #24283b;
  border-color: #3d4a71;
  color: #bb9af7;
}
.gear image {
  -gtk-icon-size: 16px;
}
.filters {
  padding: 4px 15px 10px 15px;
}
.filter {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0;
  padding: 2px 10px;
  margin-right: 4px;
  min-height: 24px;
  min-width: 0;
  color: #737aa2;
  font-size: 10px;
  letter-spacing: 0.7px;
}
.filter:hover {
  background-color: #1f2335;
  color: #a9b1d6;
}
.filter:checked {
  background-color: #24283b;
  border-color: #4c3a6e;
  color: #c8b6ff;
}
.main-page {
  background: transparent;
}
.body {
  padding: 0 12px;
}
.list-scroll {
  background: transparent;
}
.entry-list {
  background: transparent;
  padding-top: 6px;
  padding-bottom: 5px;
}
.clipd-row {
  background-color: transparent;
  border: none;
  border-radius: 0;
  margin: 1px 3px;
  padding: 0;
  outline-style: none;
  outline-width: 0;
  box-shadow: none;
}
.row-accent {
  background-color: transparent;
  border-radius: 0;
  margin: 8px 0;
}
.clipd-row:hover .row-box {
  background-color: #1f2335;
}
.clipd-row.active {
  background-color: transparent;
  border: none;
  outline-style: none;
  box-shadow: none;
}
.clipd-row.active .row-box {
  background-color: #24283b;
  border-radius: 0;
}
.clipd-row.active .row-accent {
  background-color: #bb9af7;
}
.row-box {
  padding: 6px 10px;
  min-height: 44px;
  border-radius: 0;
}
.row-icon {
  color: #737aa2;
  margin-right: 10px;
}
.clipd-row.active .row-icon {
  color: #bb9af7;
}
.row-thumb {
  margin-right: 10px;
  border: 1px solid #3d4a71;
  background-color: #12121a;
}
.row-title {
  color: #c0caf5;
  font-family: "Adwaita Sans", "Cantarell", sans-serif;
  font-size: 14.5px;
}
.row-meta {
  color: #737aa2;
  font-size: 10.5px;
  letter-spacing: 0.2px;
  margin-top: 1px;
}
.row-pin {
  color: #bb9af7;
  margin-left: 6px;
}
.more {
  opacity: 0;
  min-height: 25px;
  min-width: 25px;
  padding: 0;
  margin-left: 4px;
  border-radius: 0;
  background: transparent;
  border: none;
  color: #737aa2;
}
.more:hover {
  background-color: #2f334d;
  color: #c0caf5;
}
.detail {
  border-left: 1px solid #2f334d;
  padding: 2px 5px 0 16px;
}
.detail-kind {
  color: #bb9af7;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 1.5px;
}
.detail-meta {
  color: #737aa2;
  font-size: 10.5px;
}
.detail-title {
  color: #c0caf5;
  font-family: "Adwaita Sans", "Cantarell", sans-serif;
  font-size: 15px;
  margin-top: 6px;
}
.preview-scroll {
  background: transparent;
  margin-top: 6px;
}
.preview {
  color: #a9b1d6;
  font-size: 12px;
  padding: 2px 2px 10px 0;
}
.preview.mono {
  font-family: "JetBrainsMono Nerd Font Mono", "JetBrains Mono", monospace;
  font-size: 11.5px;
  color: #c0caf5;
}
.image-preview {
  background-color: #12121a;
  border: 1px solid #2f334d;
  margin-top: 8px;
  margin-bottom: 6px;
  min-height: 220px;
}
.actions {
  margin-top: 6px;
}
.action {
  background-color: transparent;
  border: 1px solid #3d4a71;
  border-radius: 0;
  padding: 3px 10px;
  min-height: 24px;
  min-width: 0;
  color: #a9b1d6;
  font-size: 11.5px;
}
.action:hover {
  background-color: #24283b;
  border-color: #4c3a6e;
  color: #ffffff;
}
.action.primary {
  border-color: #4c3a6e;
  color: #c8b6ff;
  background-color: #241f35;
}
.action image {
  color: inherit;
}
.footer {
  border-top: 1px solid #2f334d;
  padding: 8px 17px;
  color: #565f89;
  font-size: 10.5px;
}
.prompt-bar {
  padding: 9px 15px;
  border-top: 1px solid #2f334d;
  background-color: #1a1b26;
}
.prompt-label {
  color: #a9b1d6;
  font-size: 11px;
  letter-spacing: 0.4px;
}
.prompt-entry {
  background-color: #12121a;
  border: 1px solid #3d4a71;
  border-radius: 0;
  padding: 3px 11px;
  min-height: 26px;
  color: #c0caf5;
  font-size: 12px;
}
.prompt-entry:focus {
  border-color: #bb9af7;
  box-shadow: none;
  outline-style: none;
  outline-width: 0;
}
.prompt-button {
  background-color: transparent;
  border: 1px solid #3d4a71;
  border-radius: 0;
  padding: 3px 11px;
  min-height: 26px;
  color: #a9b1d6;
  font-size: 10.5px;
}
.prompt-button:hover {
  background-color: #24283b;
  border-color: #4c3a6e;
  color: #ffffff;
}
.prompt-button.primary {
  border-color: #4c3a6e;
  color: #c8b6ff;
  background-color: #241f35;
}
.toast {
  color: #c8b6ff;
  font-size: 10.5px;
}
.empty {
  color: #737aa2;
  font-size: 12px;
}
.empty-title {
  color: #a9b1d6;
  font-family: "Adwaita Sans", "Cantarell", sans-serif;
  font-size: 16px;
}
.help-title {
  color: #c0caf5;
  font-size: 13px;
  margin-bottom: 5px;
}
.help-key {
  color: #bb9af7;
  font-family: "JetBrainsMono Nerd Font Mono", monospace;
  font-size: 11px;
}
.help-text {
  color: #a9b1d6;
  font-size: 11px;
}
popover {
  background: transparent;
}
popover > contents {
  background-color: #1a1b26;
  border: 1px solid #3d4a71;
  border-radius: 0;
  padding: 6px;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.58);
}
.popover-action {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 6px 10px;
  color: #a9b1d6;
  font-size: 11px;
}
.popover-action:hover {
  background-color: #24283b;
  color: #ffffff;
}
.settings-page {
  background: transparent;
}
.settings-header {
  padding: 8px 15px;
  border-top: 1px solid #2f334d;
  border-bottom: 1px solid #2f334d;
  background-color: #1a1b26;
}
.settings-title {
  color: #bb9af7;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.7px;
}
.settings-back, .settings-save {
  background: transparent;
  border: 1px solid #3d4a71;
  border-radius: 0;
  padding: 4px 10px;
  min-height: 25px;
  color: #a9b1d6;
  font-size: 10.5px;
}
.settings-back:hover, .settings-save:hover {
  background-color: #24283b;
  border-color: #4c3a6e;
  color: #ffffff;
}
.settings-save {
  border-color: #4c3a6e;
  color: #c8b6ff;
  background-color: #241f35;
}
.settings-scroll {
  background: transparent;
}
.settings-form {
  padding: 12px 16px 18px 16px;
}
.settings-section {
  color: #bb9af7;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.5px;
  margin-top: 4px;
  padding-bottom: 4px;
  border-bottom: 1px solid #2f334d;
}
.settings-row {
  min-height: 30px;
}
.settings-label {
  color: #a9b1d6;
  font-size: 12px;
}
.settings-hint {
  color: #565f89;
  font-size: 10px;
}
.settings-input {
  background-color: #12121a;
  border: 1px solid #3d4a71;
  border-radius: 0;
  color: #c0caf5;
  min-height: 26px;
  font-size: 11px;
  padding: 2px 8px;
}
.settings-input:focus {
  border-color: #bb9af7;
  outline: none;
  box-shadow: none;
}
.settings-switch {
  color: #bb9af7;
}
.settings-checks {
  padding: 3px 0;
}
.settings-check {
  color: #a9b1d6;
  font-size: 11px;
}
.settings-textarea {
  background-color: #12121a;
  border: 1px solid #3d4a71;
  border-radius: 0;
  color: #c0caf5;
  font-size: 11px;
  padding: 6px;
}
.settings-textarea:focus {
  border-color: #bb9af7;
  outline: none;
}
scrollbar slider {
  background-color: #3d4a71;
  border-radius: 0;
  min-width: 4px;
  min-height: 20px;
}
scrollbar trough {
  background: transparent;
}
separator {
  background: #2f334d;
  min-height: 1px;
}
"""
