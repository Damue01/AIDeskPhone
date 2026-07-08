from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import time
import uuid
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentContext:
    permission_profile: str = "commander"
    source: str = "voice"


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    skill: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentToolResult:
    call_id: str
    skill: str
    name: str
    ok: bool
    message: str
    event: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentTurnResult:
    id: str
    input_text: str
    final_text: str
    tool_calls: list[AgentToolCall]
    tool_results: list[AgentToolResult]
    started_at: float
    finished_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "input_text": self.input_text,
            "final_text": self.final_text,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": max(0.0, self.finished_at - self.started_at),
        }


class AgentSkill(Protocol):
    name: str

    def plan(self, text: str, context: AgentContext) -> list[AgentToolCall]:
        ...

    def execute(self, call: AgentToolCall, context: AgentContext) -> AgentToolResult:
        ...


PHASE_ALIASES: tuple[tuple[str, str], ...] = (
    ("等待命令", "waiting"),
    ("待命", "waiting"),
    ("接收指令", "listening"),
    ("接收命令", "listening"),
    ("听命令", "listening"),
    ("执行命令", "executing"),
    ("执行中", "executing"),
    ("等待接听", "feedback"),
    ("回拨", "feedback"),
    ("播报结果", "reporting"),
    ("播报", "reporting"),
)


CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "北京": ("北京", "beijing", "首都"),
    "上海": ("上海", "shanghai"),
    "广州": ("广州", "guangzhou"),
    "深圳": ("深圳", "shenzhen"),
    "杭州": ("杭州", "hangzhou"),
    "成都": ("成都", "chengdu"),
    "重庆": ("重庆", "chongqing"),
    "西安": ("西安", "xian", "xi'an"),
    "南京": ("南京", "nanjing"),
    "武汉": ("武汉", "wuhan"),
    "香港": ("香港", "hong kong", "hongkong"),
    "台北": ("台北", "taipei"),
    "东京": ("东京", "tokyo"),
    "首尔": ("首尔", "seoul"),
    "新加坡": ("新加坡", "singapore"),
    "伦敦": ("伦敦", "london"),
    "巴黎": ("巴黎", "paris"),
    "纽约": ("纽约", "new york", "newyork"),
    "洛杉矶": ("洛杉矶", "los angeles", "la"),
    "旧金山": ("旧金山", "san francisco"),
    "华盛顿": ("华盛顿", "washington"),
    "莫斯科": ("莫斯科", "moscow"),
    "悉尼": ("悉尼", "sydney"),
}


NAVIGATION_HINTS: tuple[str, ...] = (
    "地球",
    "地图",
    "定位",
    "跳到",
    "切到",
    "飞到",
    "导航",
    "显示",
    "看看",
    "看一下",
    "放大到",
    "转到",
)


class CommandCenterEarthSkill:
    name = "command_center.earth"

    def plan(self, text: str, context: AgentContext) -> list[AgentToolCall]:
        del context
        clean = normalize_text(text)
        lower = clean.lower()
        calls: list[AgentToolCall] = []

        phase = find_phase_key(clean)
        if phase is not None:
            calls.append(
                AgentToolCall(
                    id=new_call_id(),
                    skill=self.name,
                    name="set_phase",
                    arguments={"phase": phase},
                )
            )

        if should_show_globe(clean, lower):
            calls.append(
                AgentToolCall(
                    id=new_call_id(),
                    skill=self.name,
                    name="show_globe",
                    arguments={"phase": "waiting"},
                )
            )

        coordinate_target = parse_coordinate_target(clean)
        if coordinate_target is not None:
            calls.append(
                AgentToolCall(
                    id=new_call_id(),
                    skill=self.name,
                    name="fly_to",
                    arguments=coordinate_target,
                )
            )
            return calls

        city = find_city(clean, lower)
        if city is not None and has_navigation_intent(clean, lower):
            calls.append(
                AgentToolCall(
                    id=new_call_id(),
                    skill=self.name,
                    name="focus_city",
                    arguments={"city": city, "zoom": 11.8},
                )
            )

        return calls

    def execute(self, call: AgentToolCall, context: AgentContext) -> AgentToolResult:
        del context
        if call.name == "set_phase":
            phase = str(call.arguments.get("phase") or "waiting")
            return self.command_result(call, "setPhase", phase, f"状态已切到 {phase}")

        if call.name == "show_globe":
            payload = {"phase": str(call.arguments.get("phase") or "waiting")}
            return self.command_result(call, "showGlobe", payload, "已返回地球屏保")

        if call.name == "focus_city":
            city = str(call.arguments.get("city") or "").strip()
            options = {"zoom": number_or_default(call.arguments.get("zoom"), 11.8)}
            return self.command_result(call, "focusCity", city, f"已定位 {city}", options=options)

        if call.name == "fly_to":
            lng = float(call.arguments["lng"])
            lat = float(call.arguments["lat"])
            label = str(call.arguments.get("label") or "指定坐标")
            zoom = number_or_default(call.arguments.get("zoom"), 9.0)
            payload = {"lng": lng, "lat": lat, "label": label, "zoom": zoom}
            return self.command_result(call, "flyTo", payload, f"已跳转到 {label}")

        return AgentToolResult(
            call_id=call.id,
            skill=call.skill,
            name=call.name,
            ok=False,
            message=f"未知地球技能：{call.name}",
        )

    def command_result(
        self,
        call: AgentToolCall,
        action: str,
        payload: Any,
        message: str,
        *,
        options: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        event = {
            "type": "command_center_command",
            "command": {
                "id": call.id,
                "source": "agent",
                "skill": self.name,
                "action": action,
                "payload": payload,
                "options": options or {},
            },
        }
        return AgentToolResult(
            call_id=call.id,
            skill=call.skill,
            name=call.name,
            ok=True,
            message=message,
            event=event,
        )


class MinimalAgentLoop:
    def __init__(self, skills: list[AgentSkill] | None = None, max_steps: int = 3) -> None:
        self.skills = skills or [CommandCenterEarthSkill()]
        self.max_steps = max(1, max_steps)

    def run(self, text: str, context: AgentContext | None = None) -> AgentTurnResult:
        started_at = time.time()
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        clean = normalize_text(text)
        ctx = context or AgentContext()
        tool_calls: list[AgentToolCall] = []
        tool_results: list[AgentToolResult] = []

        for step in range(self.max_steps):
            if step > 0:
                break
            planned = self.plan(clean, ctx)
            if not planned:
                break
            for call in planned:
                tool_calls.append(call)
                result = self.execute(call, ctx)
                tool_results.append(result)

        final_text = build_final_text(clean, tool_results)
        finished_at = time.time()
        return AgentTurnResult(
            id=turn_id,
            input_text=clean,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            started_at=started_at,
            finished_at=finished_at,
        )

    def plan(self, text: str, context: AgentContext) -> list[AgentToolCall]:
        calls: list[AgentToolCall] = []
        seen: set[tuple[str, str, str]] = set()
        for skill in self.skills:
            for call in skill.plan(text, context):
                signature = (call.skill, call.name, repr(sorted(call.arguments.items())))
                if signature in seen:
                    continue
                seen.add(signature)
                calls.append(call)
        return calls

    def execute(self, call: AgentToolCall, context: AgentContext) -> AgentToolResult:
        for skill in self.skills:
            if skill.name == call.skill:
                return skill.execute(call, context)
        return AgentToolResult(
            call_id=call.id,
            skill=call.skill,
            name=call.name,
            ok=False,
            message=f"技能未注册：{call.skill}",
        )


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def new_call_id() -> str:
    return f"tool-{uuid.uuid4().hex[:10]}"


def number_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def find_phase_key(text: str) -> str | None:
    for alias, phase in PHASE_ALIASES:
        if alias in text:
            return phase
    return None


def should_show_globe(text: str, lower: str) -> bool:
    del lower
    return ("返回" in text or "回到" in text or "切回" in text) and ("地球" in text or "屏保" in text or "主页" in text)


def has_navigation_intent(text: str, lower: str) -> bool:
    return any(hint in text for hint in NAVIGATION_HINTS) or any(hint in lower for hint in NAVIGATION_HINTS)


def find_city(text: str, lower: str) -> str | None:
    del text
    for city, aliases in CITY_ALIASES.items():
        if any(alias.lower() in lower for alias in aliases):
            return city
    return None


def parse_coordinate_target(text: str) -> dict[str, Any] | None:
    explicit = re.search(
        r"(?:经度|lng|longitude)\s*[:：]?\s*(-?\d+(?:\.\d+)?).*?(?:纬度|lat|latitude)\s*[:：]?\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if explicit:
        lng = float(explicit.group(1))
        lat = float(explicit.group(2))
        if valid_lng_lat(lng, lat):
            return {"lng": lng, "lat": lat, "label": "指定坐标", "zoom": 9.0}

    pair = re.search(r"(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)", text)
    if not pair:
        return None

    first = float(pair.group(1))
    second = float(pair.group(2))
    lng, lat = first, second
    if not valid_lng_lat(lng, lat) and valid_lng_lat(second, first):
        lng, lat = second, first
    if not valid_lng_lat(lng, lat):
        return None
    return {"lng": lng, "lat": lat, "label": "指定坐标", "zoom": 9.0}


def valid_lng_lat(lng: float, lat: float) -> bool:
    return -180.0 <= lng <= 180.0 and -85.0 <= lat <= 85.0


def build_final_text(text: str, results: list[AgentToolResult]) -> str:
    successful = [result.message for result in results if result.ok]
    failed = [result.message for result in results if not result.ok]
    if successful and not failed:
        return "首长，" + "，".join(successful) + "。"
    if successful and failed:
        return "首长，" + "，".join(successful) + "；但" + "，".join(failed) + "。"
    if failed:
        return "首长，命令没有执行成功：" + "，".join(failed) + "。"
    if text:
        return "首长，我已收到命令。当前最小 Agent 已接入地球指挥中心技能，可以处理城市定位、经纬度跳转和返回地球屏保。"
    return "首长，我没有收到有效命令。"
