# Automower BLE Plus changelog

## v0.37.0

- Choose **Random**, **Parallel**, **Checkerboard** or **Triangle** in the work area mowing editor on supported firmware.
- The integration checks the saved pattern and attempts to restore the previous mowing type if the mower rejects part of a switch from random mowing.

## v0.36.0

- Open **Mowing settings** on a work area in **Automower Maps** to change the settings the mower reports for that area, including cutting height, pattern and direction where supported.
- Each change checks the map, linked mission and previous value before writing, then reads back the confirmed settings.

## v0.35.0

- **Mowing restriction** explains why the mower is waiting, including a parking override, Frost Guard, the weekly schedule or the daily operating limit.
- When the mower sends Planner notifications, Home Assistant refreshes the restriction, next start time and active override.

## v0.34.0

- View whether TargetHeight is actively lowering the cutting height on a supported mower. The diagnostic includes timing when the firmware reports it.
- Read the current cutting height and GPS-assisted navigation status on models that provide those readings.

## v0.33.1

- The Marketplace listing, project documentation and GitHub release notes are now in English.

## v0.33.0

- Select a work area in **Automower Maps** and open **Schedule** to add, edit or remove weekly intervals. Intervals can run all day or cross midnight.
- The editor preserves other areas' schedules and warns you if the selected area's schedule changes while you are editing it.

## v0.32.0

- When creating a work area in **Automower Maps**, you can allow mowing all day, every day. This opt-in is available on models that support work area schedules.
- Existing intervals are preserved. If the mower confirms the area but the schedule is unconfirmed, the panel shows the new area and asks you to check Calendar.

## v0.31.0

- View generated lawn and AIM zone maps on six older firmware builds, including versions for the Automower 405X and 415X. Both the image entities and **Automower Maps** provide read-only access.
- Home Assistant detects over Bluetooth when one of these maps changes or becomes unavailable.

## v0.30.0

- Marketplace shows release highlights on the integration's page and when you open an update.
- The initial Home Assistant setup shows localized highlights and a link to the changelog.

[Read about earlier releases](https://github.com/olerida/automower-ble-plus/blob/main/CHANGELOG.md)

## v0.29.0

- **Automower Maps** displays supported BLE maps and lets you draw or edit zones and routes where supported.
- Select points on the map, drag their vertices or capture the mower's local position when available.

## v0.28.0

- Create and delete AIM work areas on compatible mowers. Each new work area is linked to a mowing mission you can schedule.

## v0.27.0

- Create, edit and delete AIM stay-out zones over Bluetooth when the firmware supports writing the zone map.

## v0.26.0

- Switch between a boundary wire and a virtual EPOS perimeter on compatible mowers. The form warns that the change may erase zones and schedules, and asks for confirmation.

## v0.25.0

- Set up the Husqvarna Cloud EPOS origin from an empty installation profile on compatible mowers.

## v0.24.0

- View AIM map status and progress. Start a new mapping session when supported by the firmware.

## v0.23.0

- Change the PIN from Home Assistant with confirmation of the current PIN. Available on compatible firmware, including the known Automower 420 profile.

## v0.22.0

- View EPOS positioning diagnostics and choose the docking method on compatible models.

## v0.21.0

- Drive compatible mowers from the **Automower appDrive** panel. Releasing the control sends the stop command.

## v0.19.0

- View the Automower Connect module status, signal strength and SIM alerts when the mower reports them. On the 420, you can enable or disable the module with the configured PIN.

[View all releases](https://github.com/olerida/automower-ble-plus/releases)
