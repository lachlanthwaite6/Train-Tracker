# HUB75 hardware bring-up checklist

Physical output is implemented but has not yet been verified on the final panel.

1. Identify the panel resolution, scan rate, driver IC, HUB75 connector pinout and expected current.
2. Use a dedicated regulated 5V supply sized for the panel's worst-case white output.
3. Connect supply and Pi grounds. Confirm polarity with a multimeter before applying power.
4. Use an active level-shifting adapter compatible with `hzeller/rpi-rgb-led-matrix`.
5. Begin on Raspberry Pi OS Lite/DietPi on a Pi 4 or Zero 2 W; disable onboard audio if required by
   the matrix driver's timing mode.
6. Install and run the upstream library's own demos before running Train Tracker.
7. Configure `rows`, `cols`, `chain_length`, `parallel`, `hardware_mapping`, `gpio_slowdown` and any
   required pixel mapper for the actual topology.
8. Start at low brightness. Run red, green, blue, white, gradient, checkerboard, coordinates,
   moving-border and orientation diagnostics.
9. Check for undervoltage, flicker, swapped channels, mirrored panels, hot cables and connector heat.
10. Only then run the departure scene and tune refresh/slowdown settings.

Do not claim successful hardware verification until all patterns and a sustained departure display
have been observed on the connected panel.
