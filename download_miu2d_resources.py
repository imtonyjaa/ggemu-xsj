#!/usr/bin/env python3
"""Download Miu2D resources referenced by a captured game snapshot."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_SOURCE = Path(__file__).resolve().parent / "downloads" / "miu2d.williamchan.me:10443" / "game" / "demo"
DEFAULT_BASE_URL = "https://miu2d.williamchan.me:10443/game/demo"
RESOURCE_COMMANDS = (
    "LoadMap",
    "LoadNpc",
    "LoadMapNpc",
    "MergeNpc",
    "LoadObj",
    "AddNpc",
    "LoadOneNpc",
    "AddObj",
    "PlayMusic",
    "PlaySound",
    "PlayMovie",
    "RunScript",
    "RunScirpt",
    "RunParallelScript",
    "RandRun",
    "SetTimeScript",
    "SetNpcScript",
    "SetNpcDeathScript",
    "SetAllNpcScript",
    "SetAllNpcDeathScript",
    "SetObjScript",
    "SetTrap",
    "SetMapTrap",
    "SetNpcActionFile",
    "NpcSpecialAction",
    "NpcSpecialActionEx",
    "SetNpcKind",
)
COMMAND_RE = re.compile(
    rf"\b({'|'.join(RESOURCE_COMMANDS)})\s*\(([^;\r\n]*)\)",
    re.IGNORECASE,
)
SCRIPT_FIELDS = ("script", "scriptFile", "scriptFileRight", "scriptRight", "deathScript")
ROOT_SCRIPT_FIELDS = ("timeScript", "timerScriptFile")
LOOT_SCRIPT_NAMES = (
    *(f"{level}级{kind}.txt" for level in range(1, 8) for kind in ("武器", "防具", "钱")),
    "低级药品.txt",
    "中级药品.txt",
    "高级药品.txt",
    "特级药品.txt",
)
ENGINE_REQUIRED_SPRITES = (
    "asf/interlude/die-冰.asf",
    "asf/interlude/die-毒.asf",
    "asf/interlude/die-石.asf",
)
ENGINE_OPTIONAL_SPRITES = (
    "asf/ui/common/mouse.asf",
    "asf/ui/common/panel.asf",
    "asf/ui/common/panel2.asf",
    "asf/ui/common/panel3.asf",
    "asf/ui/common/panel4.asf",
    "asf/ui/common/panel5.asf",
    "asf/ui/common/panel6.asf",
    "asf/ui/common/panel7.asf",
    "asf/ui/common/panel8.asf",
    "asf/ui/common/tipbox.asf",
    "asf/ui/dialog/panel.asf",
    "asf/ui/littlemap/panel.asf",
    "asf/ui/littlemap/主角坐标.asf",
    "asf/ui/littlemap/敌人坐标.asf",
    "asf/ui/littlemap/同伴坐标.asf",
    "asf/ui/littlemap/路人坐标.asf",
    "asf/ui/message/msgbox.asf",
    "asf/ui/option/background.asf",
    "asf/ui/option/slidebtn.asf",
    "asf/ui/timer/window.asf",
)


def encoded_path(path: str) -> str:
    """Return the URL-encoded path format used by the existing capture."""
    clean = unquote(path.replace("\\", "/")).lstrip("/")
    parts = [part for part in PurePosixPath(clean).parts if part not in {"", ".", ".."}]
    return "/".join(quote(part, safe="-._~+") for part in parts)


def strip_extension(name: str, extensions: tuple[str, ...]) -> str:
    lower = name.lower()
    for extension in extensions:
        if lower.endswith(extension):
            return name[: -len(extension)]
    return name


def scene_key(name: str) -> str:
    return strip_extension(Path(name.replace("\\", "/")).name, (".map", ".mmf"))


def as_msf(path: str) -> str:
    return re.sub(r"\.(?:asf|mpc)$", ".msf", path, flags=re.IGNORECASE)


def split_command_arguments(value: str) -> list[str]:
    """Split DSL arguments without treating commas inside strings as separators."""
    arguments = []
    current = []
    quote_char = None
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quote_char:
            current.append(char)
            escaped = True
        elif char in {'"', "'"}:
            current.append(char)
            quote_char = None if quote_char == char else char if quote_char is None else quote_char
        elif char == "," and quote_char is None:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    arguments.append("".join(current).strip())
    return arguments


def string_argument(arguments: list[str], index: int) -> str | None:
    """Return a literal string argument, leaving dynamic expressions unresolved."""
    if index >= len(arguments):
        return None
    value = arguments[index].strip()
    if len(value) < 2 or value[0] not in {'"', "'"} or value[-1] != value[0]:
        return None
    return value[1:-1].replace(f"\\{value[0]}", value[0])


@dataclass(frozen=True)
class DownloadGroup:
    candidates: tuple[str, ...]
    reason: str
    optional: bool = False


class ProgressReporter:
    def __init__(self):
        self.total = 0
        self.completed = 0
        self.counts = {
            "downloaded": 0,
            "skipped": 0,
            "known-missing": 0,
            "failed": 0,
        }
        self.interactive = sys.stdout.isatty()
        self.last_width = 0

    def add_batch(self, count: int) -> None:
        self.total += count

    def advance(self, status: str, path: str, error: str | None = None) -> None:
        self.completed += 1
        if status in self.counts:
            self.counts[status] += 1
        if error:
            self._print_failure(error)
        self._render(status, path)

    def finish(self) -> None:
        if self.interactive and self.completed:
            print()

    def _print_failure(self, error: str) -> None:
        if self.interactive and self.last_width:
            print(f"\r{' ' * self.last_width}\r", end="")
        print(f"[失败] {error}")
        self.last_width = 0

    def _render(self, status: str, path: str) -> None:
        percent = self.completed / self.total * 100 if self.total else 100
        short_path = path if len(path) <= 48 else f"…{path[-47:]}"
        status_label = {
            "downloaded": "下载成功",
            "skipped": "本地已有",
            "known-missing": "已知缺失",
            "failed": "下载失败",
        }.get(status, status)
        message = (
            f"[进度] {self.completed}/{self.total} ({percent:5.1f}%) "
            f"下载 {self.counts['downloaded']} | 已有 {self.counts['skipped']} | "
            f"已知缺失 {self.counts['known-missing']} | 失败 {self.counts['failed']} | "
            f"当前：{status_label} {short_path}"
        )
        if self.interactive:
            print(f"\r{message.ljust(self.last_width)}", end="", flush=True)
            self.last_width = max(self.last_width, len(message))
        elif self.completed == self.total or self.completed % 100 == 0:
            print(message)


class ScanReporter:
    def __init__(self):
        self.interactive = sys.stdout.isatty()
        self.last_width = 0
        self.last_completed = -1
        self.active = False

    def update(self, collector: ResourceCollector, kind: str, name: str) -> None:
        self.active = True
        completed = (
            len(collector.processed_scenes)
            + len(collector.processed_scene_npcs)
            + len(collector.processed_scene_objs)
        )
        total = (
            len(collector.scenes)
            + sum(len(names) for names in collector.scene_npcs.values())
            + sum(len(names) for names in collector.scene_objs.values())
        )
        message = (
            f"[扫描] {completed}/{total} | 场景 {len(collector.processed_scenes)} | "
            f"NPC {len(collector.processed_scene_npcs)} | 物体 {len(collector.processed_scene_objs)} | "
            f"资源 {len(collector.groups)} | 当前：{kind} {name}"
        )
        self.last_completed = completed
        if self.interactive:
            print(f"\r{message.ljust(self.last_width)}", end="", flush=True)
            self.last_width = max(self.last_width, len(message))
        elif completed == total or completed % 25 == 0:
            print(message)

    def finish(self, collector: ResourceCollector) -> None:
        if not self.active:
            return
        completed = (
            len(collector.processed_scenes)
            + len(collector.processed_scene_npcs)
            + len(collector.processed_scene_objs)
        )
        total = (
            len(collector.scenes)
            + sum(len(names) for names in collector.scene_npcs.values())
            + sum(len(names) for names in collector.scene_objs.values())
        )
        if self.interactive and self.last_completed >= 0:
            print()
        elif completed != self.last_completed:
            print(f"[扫描] {completed}/{total} | 资源 {len(collector.groups)} | 完成")
        self.active = False
        self.last_width = 0


class HttpClient:
    def __init__(self, base_url: str, timeout: float, retries: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.context = ssl.create_default_context()

    def url(self, remote_path: str) -> str:
        return f"{self.base_url}/{encoded_path(remote_path)}"

    def get(self, remote_path: str) -> tuple[bytes | None, str | None]:
        url = self.url(remote_path)
        request = Request(url, headers={"User-Agent": "Miu2D-resource-downloader/1.0"})
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout, context=self.context) as response:
                    body = response.read()
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "text/html" in content_type or body.lstrip().lower().startswith(b"<!doctype html"):
                        return None, "服务器返回了 HTML"
                    return body, None
            except HTTPError as error:
                if error.code == 404:
                    return None, "HTTP 404"
                last_error = f"HTTP {error.code}"
                if error.code < 500:
                    break
            except (URLError, TimeoutError, OSError) as error:
                last_error = str(error)
            if attempt < self.retries:
                time.sleep(min(2**attempt, 3))
        return None, last_error or "未知错误"


class ResourceCollector:
    def __init__(self):
        self.groups: dict[tuple[str, ...], DownloadGroup] = {}
        self.missing: set[str] = set()
        self.scenes: set[str] = set()
        self.scene_npcs: dict[str, set[str]] = {}
        self.scene_objs: dict[str, set[str]] = {}
        self.processed_scenes: set[str] = set()
        self.processed_scene_npcs: set[tuple[str, str]] = set()
        self.processed_scene_objs: set[tuple[str, str]] = set()
        self.scanned_script_files: set[str] = set()
        self.npc_configs: dict[str, dict[str, Any]] = {}
        self.obj_configs: dict[str, dict[str, Any]] = {}

    def add_group(self, candidates: Iterable[str], reason: str, optional: bool = False) -> None:
        normalized = tuple(dict.fromkeys(path.replace("\\", "/").lstrip("/") for path in candidates if path))
        if normalized:
            existing = self.groups.get(normalized)
            if existing is None or (existing.optional and not optional):
                self.groups[normalized] = DownloadGroup(normalized, reason, optional)

    def add_sprite(self, name: str | None, directories: tuple[str, ...], reason: str) -> None:
        if not name:
            return
        normalized = name.replace("\\", "/").lstrip("/")
        if "/" in normalized:
            candidates = [as_msf(normalized)]
        else:
            candidates = [as_msf(f"{directory}/{normalized}") for directory in directories]
        self.add_group(candidates, reason)

    def add_sound(self, name: str | None, reason: str) -> None:
        if not name:
            return
        normalized = name.replace("\\", "/").lstrip("/")
        if not normalized.startswith("content/sound/"):
            normalized = f"content/sound/{Path(normalized).name}"
        extension = Path(normalized).suffix.lower()
        candidates = [normalized]
        if extension in {".wav", ".mp3", ".ogg"}:
            candidates.insert(0, str(PurePosixPath(normalized).with_suffix(".xnb")))
        self.add_group(candidates, reason)

    def add_music(self, name: str | None, reason: str) -> None:
        if not name:
            return
        base = strip_extension(Path(name.replace("\\", "/")).name.lower(), (".mp3", ".wma", ".ogg", ".wav"))
        self.add_group((f"content/music/{base}.ogg", f"content/music/{base}.mp3"), reason)

    def add_video(self, name: str | None, reason: str) -> None:
        if not name:
            return
        base = strip_extension(
            Path(name.replace("\\", "/")).name.lower(),
            (".avi", ".wmv", ".mov", ".mp4", ".webm"),
        )
        self.add_group((f"content/video/{base}.webm",), reason)

    def add_script(self, name: str | None, owner_scene: str | None, reason: str) -> None:
        if not name:
            return
        normalized = name.replace("\\", "/").lstrip("/")
        if normalized.startswith("script/"):
            self.add_group((normalized,), reason)
            return
        candidates = []
        if owner_scene:
            candidates.append(f"script/map/{owner_scene}/{Path(normalized).name}")
        candidates.extend((f"script/common/{Path(normalized).name}", f"script/goods/{Path(normalized).name}"))
        self.add_group(candidates, reason)

    def add_root_script(self, name: str | None, reason: str) -> None:
        if not name:
            return
        normalized = name.replace("\\", "/").lstrip("/")
        if normalized.startswith("script/"):
            self.add_group((normalized,), reason)
        else:
            self.add_group((f"script/{normalized}",), reason)

    def add_scene(self, name: str | None) -> str | None:
        if not name:
            return None
        key = scene_key(name)
        if key:
            self.scenes.add(key)
            self.add_group((f"map/littlemap/{key}.png",), "运行时小地图", optional=True)
            return key
        return None

    def add_scene_npc(self, scene: str | None, name: str | None) -> None:
        if scene and name:
            self.scene_npcs.setdefault(scene, set()).add(Path(name.replace("\\", "/")).name)

    def add_scene_obj(self, scene: str | None, name: str | None) -> None:
        if scene and name:
            self.scene_objs.setdefault(scene, set()).add(Path(name.replace("\\", "/")).name)

    def scan_script(self, text: str | None, owner_scene: str | None) -> None:
        if not text:
            return
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"//[^\r\n]*", "", text)
        current_scene = owner_scene
        for match in COMMAND_RE.finditer(text):
            command, raw_arguments = match.groups()
            command = command.lower()
            arguments = split_command_arguments(raw_arguments)
            value = string_argument(arguments, 0)
            if command == "loadmap":
                current_scene = self.add_scene(value)
            elif command in {"loadnpc", "loadmapnpc", "mergenpc"}:
                self.add_scene_npc(current_scene, value)
            elif command == "loadobj":
                self.add_scene_obj(current_scene, value)
            elif command in {"addnpc", "loadonenpc"}:
                self._scan_config_entity(self.npc_configs, value, current_scene, "NPC 配置")
            elif command == "addobj":
                self._scan_config_entity(self.obj_configs, value, current_scene, "物体配置")
            elif command == "playmusic":
                self.add_music(value, "场景脚本音乐")
            elif command == "playsound":
                self.add_sound(value, "场景脚本音效")
            elif command == "playmovie":
                self.add_video(value, "场景脚本视频")
            elif command in {"runscript", "runscirpt", "runparallelscript"}:
                self.add_script(value, current_scene, "场景脚本依赖")
            elif command == "randrun":
                self.add_script(string_argument(arguments, 1), current_scene, "随机脚本依赖")
                self.add_script(string_argument(arguments, 2), current_scene, "随机脚本依赖")
            elif command == "settimescript":
                self.add_script(string_argument(arguments, 1), current_scene, "计时器脚本")
            elif command in {
                "setnpcscript",
                "setnpcdeathscript",
                "setallnpcscript",
                "setallnpcdeathscript",
                "setobjscript",
            }:
                self.add_script(string_argument(arguments, 1), current_scene, "动态实体脚本")
            elif command == "settrap":
                trap_scene = self.add_scene(string_argument(arguments, 0)) or current_scene
                self.add_script(string_argument(arguments, 2), trap_scene, "动态陷阱脚本")
            elif command == "setmaptrap":
                self.add_script(string_argument(arguments, 1), current_scene, "动态陷阱脚本")
            elif command == "setnpcactionfile":
                self.add_sprite(string_argument(arguments, 2), ("asf/character", "asf/interlude"), "NPC 动作")
            elif command in {"npcspecialaction", "npcspecialactionex"}:
                self.add_sprite(string_argument(arguments, 1), ("asf/character", "asf/interlude"), "NPC 特殊动作")
            elif command == "setnpckind" and len(arguments) > 1 and arguments[1].strip() == "3":
                name = string_argument(arguments, 0)
                self.add_sprite(name and f"{name}.asf", ("asf/ui/littlehead",), "队友头像")

    def scan_config(self, config: dict[str, Any]) -> None:
        initial_scene = self.add_scene(config.get("initialMap"))
        self.add_scene_npc(initial_scene, config.get("initialNpc"))
        self.add_scene_obj(initial_scene, config.get("initialObj"))
        self.add_music(config.get("initialBgm"), "初始背景音乐")
        self.add_music(config.get("titleMusic"), "标题音乐")
        self.scan_script(config.get("newGameScript"), initial_scene)
        self._scan_config_value(config.get("uiTheme"), None)

    def _scan_config_value(self, value: Any, key: str | None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                self._scan_config_value(child_value, child_key)
            return
        if isinstance(value, list):
            for child in value:
                self._scan_config_value(child, key)
            return
        if not isinstance(value, str) or not value:
            return
        suffix = Path(value).suffix.lower()
        lower_key = (key or "").lower()
        if suffix in {".asf", ".mpc", ".msf", ".jpg", ".jpeg", ".png"}:
            self.add_sprite(value, ("asf/ui",), "UI 资源")
        elif "sound" in lower_key or suffix in {".wav", ".xnb"}:
            self.add_sound(value, "UI 音效")

    def scan_data(self, data: dict[str, Any]) -> None:
        for good in data.get("goods", []):
            self.add_sprite(good.get("image"), ("asf/goods",), "物品图片")
            self.add_sprite(good.get("icon"), ("asf/goods",), "物品图标")
            self.add_sprite(good.get("superModeImage"), ("asf/goods",), "物品特殊图片")
            self.add_script(good.get("script"), None, "物品脚本")

        magics = data.get("magics", {})
        all_magics = [*magics.get("player", []), *magics.get("npc", [])]
        for magic in all_magics:
            self._scan_magic_value(magic)

        npcs = data.get("npcs", {})
        self.npc_configs = self._config_index(npcs.get("npcs", []))
        for entry in [*npcs.get("npcs", []), *npcs.get("resources", [])]:
            for resource in (entry.get("resources") or {}).values():
                if isinstance(resource, dict):
                    self.add_sprite(resource.get("image"), ("asf/character", "asf/interlude"), "NPC 动画")
                    self.add_sound(resource.get("sound"), "NPC 音效")

        objs = data.get("objs", {})
        self.obj_configs = self._config_index(objs.get("objs", []))
        for entry in [*objs.get("objs", []), *objs.get("resources", [])]:
            for resource in (entry.get("resources") or {}).values():
                if isinstance(resource, dict):
                    self.add_sprite(resource.get("image"), ("asf/object",), "物体动画")
                    self.add_sound(resource.get("sound"), "物体音效")

        players = data.get("players", [])
        player_indices = {self._player_resource_index(player.get("npcIni")) for player in players}
        for player in players:
            self._scan_entity_scripts(player, None, "玩家")
            name = player.get("name")
            self.add_sprite(name and f"{name}.asf", ("asf/ui/littlehead",), "玩家头像")
        for magic in all_magics:
            if magic.get("attackFile") and magic.get("actionFile"):
                for index in player_indices:
                    self.add_sprite(
                        f"{magic['actionFile']}{index}.asf",
                        ("asf/character", "asf/interlude"),
                        "武功角色动作",
                    )

        for portrait in data.get("portraits", []):
            self.add_sprite(portrait.get("asfFile"), ("asf/portrait",), "头像")

        self._scan_runtime_dependencies()

    @staticmethod
    def _config_index(entries: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            Path(entry["key"].replace("\\", "/")).name.lower(): entry
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("key"), str)
        }

    @staticmethod
    def _player_resource_index(name: Any) -> int:
        if not isinstance(name, str):
            return 1
        match = re.search(r"(\d+)\.ini$", name, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 1

    def _scan_config_entity(
        self,
        configs: dict[str, dict[str, Any]],
        name: str | None,
        owner_scene: str | None,
        entity_type: str,
    ) -> None:
        if not name:
            return
        key = Path(name.replace("\\", "/")).name.lower()
        entry = configs.get(key)
        if entry:
            self._scan_entity_scripts(entry, owner_scene, entity_type)

    def _scan_entity_scripts(
        self,
        entry: dict[str, Any],
        owner_scene: str | None,
        entity_type: str,
    ) -> None:
        for field in SCRIPT_FIELDS:
            self.add_script(entry.get(field), owner_scene, f"{entity_type} {field}")
        for field in ROOT_SCRIPT_FIELDS:
            self.add_root_script(entry.get(field), f"{entity_type} {field}")

    def _scan_runtime_dependencies(self) -> None:
        for name in LOOT_SCRIPT_NAMES:
            self.add_group((f"script/common/{name}",), "引擎动态掉落脚本", optional=True)
        for path in ENGINE_REQUIRED_SPRITES:
            self.add_group((as_msf(path),), "引擎运行时资源")
        for path in ENGINE_OPTIONAL_SPRITES:
            self.add_group((as_msf(path),), "引擎可选 UI 资源", optional=True)
        self.add_video("team.webm", "标题团队视频")

    def _scan_magic_value(self, value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                self._scan_magic_value(child_value, child_key)
            return
        if isinstance(value, list):
            for child in value:
                self._scan_magic_value(child, key)
            return
        if not isinstance(value, str) or not value:
            return
        lower_key = (key or "").lower()
        if lower_key in {"image", "icon"}:
            self.add_sprite(value, ("asf/magic",), "武功图片")
        elif "image" in lower_key:
            self.add_sprite(value, ("asf/effect",), "武功特效")
        elif "sound" in lower_key:
            self.add_sound(value, "武功音效")

    def scan_manifest(self, manifest: dict[str, Any], owner_scene: str) -> None:
        for path in manifest.get("missing", []):
            self.missing.add(path.replace("\\", "/").lstrip("/"))
        for path in manifest.get("tiles", []):
            self.add_group((path,), "地图瓦片")
        for script_map in (manifest.get("scripts", {}), manifest.get("traps", {})):
            if isinstance(script_map, dict):
                for text in script_map.values():
                    self.scan_script(text, owner_scene)

    def scan_scene_entities(self, entries: list[dict[str, Any]], owner_scene: str, entity_type: str) -> None:
        for entry in entries:
            self.add_sound(entry.get("wavFile"), f"{entity_type} 环境音")
            self._scan_entity_scripts(entry, owner_scene, entity_type)


class Downloader:
    def __init__(self, source: Path, output: Path, client: HttpClient, dry_run: bool):
        self.source = source.resolve()
        self.output = output.resolve()
        self.client = client
        self.dry_run = dry_run
        self.lock = Lock()
        self.downloaded: list[str] = []
        self.skipped: list[str] = []
        self.known_missing: list[str] = []
        self.failed: list[dict[str, Any]] = []

    def local_path(self, remote_path: str) -> Path:
        return self.output / encoded_path(remote_path)

    def existing_path(self, remote_path: str) -> Path | None:
        relative = encoded_path(remote_path)
        for root in dict.fromkeys((self.output, self.source)):
            candidate = root / relative
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None

    def read_json(self, remote_path: str) -> Any | None:
        path = self.existing_path(remote_path)
        if path:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
        if self.dry_run:
            return None
        body, error = self.client.get(remote_path)
        if body is None:
            self.failed.append({"candidates": [remote_path], "reason": "API", "error": error})
            return None
        self._write(remote_path, body)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.failed.append({"candidates": [remote_path], "reason": "API JSON", "error": "JSON 解析失败"})
            return None

    def read_existing_json(self, remote_path: str) -> Any | None:
        path = self.existing_path(remote_path)
        if not path:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def download_group(self, group: DownloadGroup, missing: set[str]) -> tuple[str, str | None]:
        if any(self.existing_path(f"resources/{candidate}") for candidate in group.candidates):
            with self.lock:
                self.skipped.append(group.candidates[0])
            return "skipped", None
        candidates = [candidate for candidate in group.candidates if candidate not in missing]
        if not candidates:
            with self.lock:
                self.known_missing.append(group.candidates[0])
            return "known-missing", None
        if self.dry_run:
            return "planned", None
        errors = []
        all_not_found = True
        for candidate in candidates:
            remote_path = f"resources/{candidate}"
            body, error = self.client.get(remote_path)
            if body is not None:
                self._write(remote_path, body)
                return "downloaded", None
            all_not_found = all_not_found and error == "HTTP 404"
            errors.append(f"{self.client.url(remote_path)} -> {error}")
        if group.optional and all_not_found:
            with self.lock:
                self.known_missing.append(group.candidates[0])
            return "known-missing", None
        error_message = "; ".join(errors)
        with self.lock:
            self.failed.append({"candidates": candidates, "reason": group.reason, "error": error_message})
        return "failed", error_message

    def _write(self, remote_path: str, body: bytes) -> None:
        target = self.local_path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        temporary.write_bytes(body)
        temporary.replace(target)
        with self.lock:
            self.downloaded.append(remote_path)


def load_required_json(source: Path, relative: str) -> dict[str, Any]:
    path = source / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"缺少必要文件：{path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"JSON 格式错误：{path}: {error}") from error


def discover_scenes(
    collector: ResourceCollector,
    downloader: Downloader,
    reporter: ScanReporter | None = None,
) -> None:
    while True:
        pending_scenes = sorted(collector.scenes - collector.processed_scenes)
        pending_npcs = sorted(
            (scene, name)
            for scene, names in collector.scene_npcs.items()
            for name in names
            if (scene, name) not in collector.processed_scene_npcs
        )
        pending_objs = sorted(
            (scene, name)
            for scene, names in collector.scene_objs.items()
            for name in names
            if (scene, name) not in collector.processed_scene_objs
        )
        if not pending_scenes and not pending_npcs and not pending_objs:
            return
        for scene in pending_scenes:
            collector.processed_scenes.add(scene)
            if reporter:
                reporter.update(collector, "场景", scene)
            encoded_scene = encoded_path(scene)
            manifest_remote = f"api/scenes/{encoded_scene}/manifest"
            manifest_local = f"api/scenes/{encoded_scene}/manifest.json"
            manifest = downloader.read_existing_json(manifest_local)
            if manifest is None and not downloader.dry_run:
                body, error = downloader.client.get(manifest_remote)
                if body is not None:
                    downloader._write(manifest_local, body)
                    try:
                        manifest = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        manifest = None
                elif error != "HTTP 404":
                    downloader.failed.append({"candidates": [manifest_remote], "reason": "场景 manifest", "error": error})
            if isinstance(manifest, dict):
                collector.scan_manifest(manifest, scene)

            mmf_local = f"api/scenes/{encoded_scene}/mmf.bin"
            if not downloader.existing_path(mmf_local) and not downloader.dry_run:
                body, error = downloader.client.get(f"api/scenes/{encoded_scene}/mmf")
                if body is not None:
                    downloader._write(mmf_local, body)
                elif error != "HTTP 404":
                    downloader.failed.append({"candidates": [f"api/scenes/{encoded_scene}/mmf"], "reason": "场景地图", "error": error})

        for scene, name in pending_npcs:
            collector.processed_scene_npcs.add((scene, name))
            if reporter:
                reporter.update(collector, "NPC", f"{scene}/{name}")
            remote = f"api/scenes/npc/{encoded_path(scene)}/{encoded_path(name)}"
            entries = downloader.read_json(remote)
            if isinstance(entries, list):
                collector.scan_scene_entities(entries, scene, "NPC")

        for scene, name in pending_objs:
            collector.processed_scene_objs.add((scene, name))
            if reporter:
                reporter.update(collector, "物体", f"{scene}/{name}")
            remote = f"api/scenes/obj/{encoded_path(scene)}/{encoded_path(name)}"
            entries = downloader.read_json(remote)
            if isinstance(entries, list):
                collector.scan_scene_entities(entries, scene, "物体")


def scan_local_scripts(collector: ResourceCollector, roots: Iterable[Path]) -> int:
    scanned = 0
    for root in roots:
        script_root = root / "resources" / "script"
        if not script_root.is_dir():
            continue
        for path in script_root.rglob("*.txt"):
            relative = path.relative_to(root).as_posix()
            if relative in collector.scanned_script_files:
                continue
            collector.scanned_script_files.add(relative)
            parts = path.relative_to(root).parts
            owner_scene = unquote(parts[3]) if len(parts) > 4 and parts[0:3] == ("resources", "script", "map") else None
            try:
                collector.scan_script(path.read_text(encoding="utf-8", errors="replace"), owner_scene)
                scanned += 1
            except OSError:
                continue
    return scanned


def parse_base_url(value: str) -> str:
    if value.startswith("[") or "](" in value:
        raise argparse.ArgumentTypeError("请传入纯 URL，不要使用 Markdown 链接格式")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"URL 格式错误：{error}") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("URL 必须是完整的 http:// 或 https:// 地址")
    return value.rstrip("/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 Miu2D 抓取快照中引用但尚未下载的资源")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="已有抓取目录")
    parser.add_argument("--output", type=Path, required=True, help="完整资源输出目录")
    parser.add_argument("--base-url", type=parse_base_url, default=DEFAULT_BASE_URL, help="游戏服务根 URL")
    parser.add_argument("--workers", type=int, default=12, help="并发下载数，默认 12")
    parser.add_argument("--timeout", type=float, default=20, help="单次请求超时秒数")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    parser.add_argument("--no-copy-existing", action="store_true", help="不把已有文件复制到输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只扫描并显示计划，不发起网络请求")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        print(f"源目录不存在：{source}", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("--workers 必须大于 0", file=sys.stderr)
        return 2
    if output != source and output.is_relative_to(source):
        print("输出目录不能位于源目录内部，否则复制已有文件时会递归", file=sys.stderr)
        return 2

    if not args.dry_run and not args.no_copy_existing and source != output:
        shutil.copytree(source, output, dirs_exist_ok=True)
    if not args.dry_run:
        output.mkdir(parents=True, exist_ok=True)

    config = load_required_json(source, "api/config.json")
    data = load_required_json(source, "api/data.json")
    collector = ResourceCollector()
    collector.scan_data(data)
    collector.scan_config(config)

    client = HttpClient(args.base_url, args.timeout, args.retries)
    downloader = Downloader(source, output, client, args.dry_run)
    scan_local_scripts(collector, (output, source))
    scan_reporter = ScanReporter()
    print("[扫描] 正在发现场景和资源依赖，总数可能随新依赖增长…")
    discover_scenes(collector, downloader, scan_reporter)
    scan_reporter.finish(collector)

    groups = list(collector.groups.values())
    existing_count = sum(
        any(downloader.existing_path(f"resources/{path}") for path in group.candidates)
        for group in groups
    )
    print(f"发现 {len(collector.scenes)} 个场景、{len(groups)} 组资源；已有 {existing_count} 组")
    if args.dry_run:
        for group in groups:
            if not any(downloader.existing_path(f"resources/{path}") for path in group.candidates):
                print(f"[计划] {' | '.join(group.candidates)} ({group.reason})")
        return 0

    processed_groups: set[tuple[str, ...]] = set()
    progress = ProgressReporter()
    while True:
        discover_scenes(collector, downloader, scan_reporter)
        scan_reporter.finish(collector)
        pending_groups = [
            group for key, group in collector.groups.items()
            if key not in processed_groups
        ]
        if pending_groups:
            progress.add_batch(len(pending_groups))
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(downloader.download_group, group, collector.missing): group
                    for group in pending_groups
                }
                for future in as_completed(futures):
                    group = futures[future]
                    status, error = future.result()
                    progress.advance(status, group.candidates[0], error)
            processed_groups.update(group.candidates for group in pending_groups)
        newly_scanned = scan_local_scripts(collector, (output, source))
        if not pending_groups and newly_scanned == 0:
            break
    progress.finish()

    groups = list(collector.groups.values())

    report = {
        "baseUrl": args.base_url,
        "source": str(source),
        "output": str(output),
        "sceneCount": len(collector.scenes),
        "resourceGroupCount": len(groups),
        "downloadedCount": len(downloader.downloaded),
        "skippedCount": len(downloader.skipped),
        "knownMissingCount": len(downloader.known_missing),
        "failedCount": len(downloader.failed),
        "downloaded": downloader.downloaded,
        "knownMissing": downloader.known_missing,
        "failed": downloader.failed,
    }
    report_path = output / "download-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"完成：下载 {len(downloader.downloaded)}，跳过 {len(downloader.skipped)}，"
        f"已知缺失 {len(downloader.known_missing)}，失败 {len(downloader.failed)}"
    )
    print(f"报告：{report_path}")
    return 1 if downloader.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
