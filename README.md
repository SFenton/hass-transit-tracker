# Transit Tracker - Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for managing [Transit Tracker](https://github.com/tjhorner/transit-tracker) displays.

## Features

- **Auto-discovery** of Transit Tracker ESPHome devices
- **Per-route visibility switches** — toggle individual routes on/off from the HA dashboard
- **Dynamic updates** — switches are automatically created/removed as routes change
- **Route name display** — human-readable route names from the firmware's text sensor

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "Transit Tracker" and install
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/transit_tracker` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Transit Tracker**
3. The integration will auto-discover your Transit Tracker device, or you can enter the entity ID manually

## How It Works

The integration reads your Transit Tracker's schedule configuration and creates a switch entity for each route. Turning a switch **off** hides that route from the LED display; turning it **on** shows it again.

Route visibility is managed via the firmware's `hidden_routes_config` text entity — the integration writes hidden route IDs back to the device in real time.

## Requirements

- Home Assistant 2024.1+
- Transit Tracker with firmware supporting:
  - `hidden_routes_config` text entity
  - `route_names` text sensor (optional, for human-readable names)

## License

MIT
