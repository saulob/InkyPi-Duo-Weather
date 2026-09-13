# Duo Weather Plugin for InkyPi

Display current weather and hourly forecasts with dynamic condition-based backgrounds, visually inspired by the iPhone Duo weather interface. Designed for quick readability and optimized for e-paper displays

A weather plugin inspired by the iPhone Duo Weather widget: current condition, high/low, location time, a large current temperature, and an hourly forecast row.

Duo Weather keeps the same configuration and weather providers as Mini Weather, with an independent layout focused on a full-bleed sky background and hourly data.

## Features

- Current weather with icon, condition text, and large temperature
- Daily high and low temperatures
- Current time based on the configured time zone
- Hourly forecast sampled from real provider hourly data
- Support for multiple languages
- Location selection via coordinates or quick presets
- Time zone handling based on location or device
- Unit selection (Celsius, Fahrenheit, or Kelvin)
- Weather providers: Open-Meteo (no API key required) or OpenWeatherMap (API key required)

## Settings

- Language selection with localized condition labels
- Quick Location presets or manual latitude and longitude
- Weather provider: Open-Meteo or OpenWeatherMap (requires API key)
- Unit selection Celsius, Fahrenheit, or Kelvin
- Title mode: location or custom text
- Show or hide weather icons
- Optional colorful icons
- Time zone selection: location time zone or local time zone

## Notes

- Uses Open-Meteo free API (no key required) or OpenWeatherMap (API key required)
- Hourly points are selected from provider hourly forecasts, not from daily forecasts
- Sky background is rendered locally with PIL from condition-specific presets
- Presets add local glow, soft clouds, haze, precipitation, stars, and subtle lightning effects
- Unknown conditions use the `default` atmospheric preset
- Designed for e-paper contrast and different screen sizes
