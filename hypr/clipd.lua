-- clipd: context-aware clipboard manager
-- Add to ~/.config/hypr/config/ and require it from hyprland.lua, or copy
-- these lines into your keybinds/settings/autostart files.

hl.bind("SUPER + V", hl.dsp.exec_cmd("clipd toggle"))

hl.layer_rule({
  match = { namespace = "clipd" },
  blur = true,
  ignore_alpha = 0.35,
  no_anim = true,
})

hl.on("hyprland.start", function()
  hl.exec_cmd("systemctl --user start clipd")
end)
