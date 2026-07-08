# AiLandLine Command Center API

页面加载后会暴露两个等价的全局对象：

```js
window.AiLandLineConsole
window.AiLandLine
```

推荐本地 AI 控制台优先使用 `window.AiLandLineConsole`。如果控制台运行在浏览器扩展、自动化隔离域、iframe 或其他拿不到页面全局对象的环境里，使用下面的 `CustomEvent` 或 `postMessage` 接口。

所有直接方法都会返回一个普通对象，成功时包含 `ok: true`，失败时包含 `ok: false` 和 `reason`。

## Ready

如果脚本在页面加载前注入，可以监听 ready 事件：

```js
window.addEventListener("ailandline:ready", (event) => {
  console.log(event.detail);
});
```

如果脚本在页面加载后运行，直接判断：

```js
if (window.AiLandLineConsole?.ready) {
  window.AiLandLineConsole.pushLog("[AI] 控制台已接管");
}
```

## Methods

```js
const api = window.AiLandLineConsole;
```

### pushLog(message)

向底部日志流追加一条日志。

```js
api.pushLog("[CMD] 打开浏览器");
```

### setPhase(phase)

设置屏幕右侧状态。可用值：

- `waiting`
- `executing`
- `feedback`
- `reporting`

```js
api.setPhase("executing");
```

`setStatus(phase)` 是同一个方法的别名。

### setSource(source)

设置右侧来源文字。

```js
api.setSource("本地 AI 控制台");
```

### focusCity(city, options)

跳转到内置城市，支持中文名或英文名。

```js
api.focusCity("东京");
api.focusCity("New York", { zoom: 12.6, duration: 1800 });
```

### flyTo(target, options)

跳转到经纬度位置。`navigate(target, options)` 是别名。

```js
api.flyTo({
  lng: 116.4074,
  lat: 39.9042,
  zoom: 12.8,
  label: "北京"
});
```

也可以传 `center`：

```js
api.flyTo({
  center: [121.4737, 31.2304],
  label: "上海",
  zoom: 13,
  duration: 1600
});
```

### focusPlace(place, options)

用于地点跳转。当前页面不做联网地理编码；如果是非内置城市，建议传经纬度。

```js
api.focusPlace({
  lng: 139.7528,
  lat: 35.6852,
  label: "东京站",
  zoom: 15
});
```

### showGlobe(options)

回到地球屏保视图。

```js
api.showGlobe();
api.showGlobe({ keepScale: false, phase: "waiting" });
```

### getState()

读取当前页面状态。

```js
const state = api.getState();
```

### getCities()

读取内置城市列表。

```js
const cities = api.getCities();
```

### getPhases()

读取可用状态列表。

```js
const phases = api.getPhases();
```

### setVisualPreset(preset)

Switch the globe visual experiment preset. Available values:

- `haze`
- `cinematic`
- `scientific`
- `orbital`

```js
api.setVisualPreset("cinematic");
api.setVisualPreset({ preset: "scientific" });
```

### cycleVisualPreset(step)

Cycle through the available visual presets. This is useful for local preview and AI-driven visual feedback.

```js
api.cycleVisualPreset();
api.cycleVisualPreset(-1);
```

### getVisualPresets()

Read the available visual presets.

```js
const presets = api.getVisualPresets();
```

### invoke(action, payload, options)

统一命令入口，适合本地 AI 控制台做动作分发。

```js
api.invoke("focusCity", "纽约");
api.invoke("setPhase", "feedback");
api.invoke("flyTo", { lng: -0.1276, lat: 51.5072, label: "伦敦" });
api.invoke("fx", "orbital");
api.invoke("showGlobe");
```

## Events

不方便直接拿全局对象时，也可以发事件。命令执行后页面会派发 `ailandline:result`。

```js
window.addEventListener("ailandline:result", (event) => {
  console.log(event.detail.result);
});

window.dispatchEvent(new CustomEvent("ailandline:log", {
  detail: { message: "[AI] 正在分析屏幕" }
}));

window.dispatchEvent(new CustomEvent("ailandline:phase", {
  detail: { phase: "reporting" }
}));

window.dispatchEvent(new CustomEvent("ailandline:navigate", {
  detail: { city: "北京" }
}));

window.dispatchEvent(new CustomEvent("ailandline:command", {
  detail: {
    requestId: "cmd-001",
    action: "flyTo",
    payload: { lng: 2.3522, lat: 48.8566, label: "巴黎" },
    options: { zoom: 12.5 }
  }
}));
```

## postMessage

如果 AI 控制台处在内容脚本或隔离执行域里，推荐用 `postMessage`。页面会用同一个 `requestId` 回传结果。

```js
window.addEventListener("message", (event) => {
  const data = event.data;
  if (data?.channel === "AiLandLineConsole" && data.type === "ailandline:result") {
    console.log(data.requestId, data.result);
  }
});

window.postMessage({
  channel: "AiLandLineConsole",
  type: "ailandline:command",
  requestId: "nav-001",
  action: "focusCity",
  payload: "伦敦",
  options: { zoom: 12.4, duration: 1600 }
}, window.location.origin);
```

## URL Parameters

本地调试时也可以直接通过 URL 触发一次跳转：

```text
http://127.0.0.1:8790/?city=东京
http://127.0.0.1:8790/?lng=116.4074&lat=39.9042&label=北京&zoom=12.8
http://127.0.0.1:8790/?fx=cinematic
```
