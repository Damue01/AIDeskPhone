---
name: command-center-earth
description: Use for AI Desk Phone command-center map and globe operations, including returning to the Earth home screen, focusing a city, flying to coordinates, and switching command-center phases.
---

# Command Center Earth

Use this skill when the user asks to operate the command-center Earth or map view.

The phone Agent does not manipulate the browser DOM directly. It emits a `command_center_command` event. The command-center page listens to that event and performs the visual action.

## Available Actions

- `showGlobe`: Return to the default Earth home/screen-saver view.
- `focusCity`: Move the map/globe to a named city.
- `flyTo`: Move the map/globe to explicit longitude and latitude.
- `setPhase`: Switch the command-center status phase.

## Event Shape

Tool results should publish:

```json
{
  "type": "command_center_command",
  "command": {
    "id": "tool-call-id",
    "source": "agent",
    "skill": "command_center.earth",
    "action": "focusCity",
    "payload": "上海",
    "options": {
      "zoom": 11.8
    }
  }
}
```

For coordinates, use:

```json
{
  "action": "flyTo",
  "payload": {
    "lng": 121.4737,
    "lat": 31.2304,
    "label": "上海",
    "zoom": 9
  }
}
```

## Routing Rules

- "回到地球", "回首页", "默认首页", "地球屏保": call `showGlobe`.
- "定位到上海", "切到北京", "地图去广州": call `focusCity`.
- "跳到 121.47,31.23", "经纬度": call `flyTo`.
- "待命", "接收命令", "执行中", "回拨", "播报": call `setPhase`.
- If the user gives a new city after a previous map operation, treat it as a fresh `focusCity`; do not assume the old city remains locked.

## Phone Reply Style

After a successful map operation, the spoken reply should be short and role-consistent, for example:

- "首长，已定位上海。"
- "首长，已回到地球首页。"
- "首长，已跳转到指定坐标。"

Do not mention JSON, event names, skill names, or implementation details in the spoken reply.
