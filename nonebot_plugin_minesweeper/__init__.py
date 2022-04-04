import re
import shlex
import asyncio
from io import BytesIO
from dataclasses import dataclass
from asyncio import TimerHandle
from typing import Dict, List, Tuple, Union, Optional, NoReturn

from nonebot.matcher import Matcher
from nonebot.exception import ParserExit
from nonebot.rule import to_me, ArgumentParser
from nonebot import on_command, on_shell_command
from nonebot.params import ShellCommandArgv, CommandArg
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    GroupMessageEvent,
    Message,
    MessageSegment,
)

from .data_source import MineSweeper, GameState
from .utils import skin_list


__help__plugin_name__ = "minesweeper"
__des__ = "扫雷游戏"
__cmd__ = f"""
@我 + “扫雷”开始游戏；
@我 + 扫雷初级 / 扫雷中级 / 扫雷高级 可开始不同难度的游戏；
可使用 -r/--row ROW 、-c/--col COL 、-n/--num NUM 自定义行列数和雷数；
可使用 -s/--skin SKIN 指定皮肤，默认为 winxp；
使用 “挖开”+位置 来挖开方块，可同时指定多个位置；
使用 “标记”+位置 来标记方块，可同时指定多个位置；
位置为 字母+数字 的组合，如“A1”
""".strip()
__short_cmd__ = "@我 扫雷"
__example__ = """
@小Q 扫雷
挖开 A1
标记 B2 C3
""".strip()
__usage__ = f"{__des__}\n\nUsage:\n{__cmd__}\n\nExample:\n{__example__}"


parser = ArgumentParser("minesweeper", description="扫雷")
parser.add_argument("-r", "--row", type=int, help="行数")
parser.add_argument("-c", "--col", type=int, help="列数")
parser.add_argument("-n", "--num", type=int, help="雷数")
parser.add_argument("-s", "--skin", default="winxp", help="皮肤")
parser.add_argument("--show", action="store_true", help="显示游戏盘")
parser.add_argument("--stop", action="store_true", help="结束游戏")
parser.add_argument("--open", nargs="*", help="挖开方块")
parser.add_argument("--mark", nargs="*", help="标记方块")


@dataclass
class Options:
    row: int = 0
    col: int = 0
    num: int = 0
    skin: str = ""
    show: bool = False
    stop: bool = False
    open: List[str] = []
    mark: List[str] = []


games: Dict[str, MineSweeper] = {}
timers: Dict[str, TimerHandle] = {}

minesweeper = on_shell_command("minesweeper", parser=parser, block=True, priority=13)


@minesweeper.handle()
async def _(
    matcher: Matcher, event: MessageEvent, argv: List[str] = ShellCommandArgv()
):
    await handle_minesweeper(matcher, event, argv)


def get_cid(event: MessageEvent):
    return (
        f"group_{event.group_id}"
        if isinstance(event, GroupMessageEvent)
        else f"private_{event.user_id}"
    )


def game_running(event: MessageEvent) -> bool:
    cid = get_cid(event)
    return bool(games.get(cid, None))


def shortcut(cmd: str, argv: List[str] = [], **kwargs):
    command = on_command(cmd, **kwargs, block=True, priority=12)

    @command.handle()
    async def _(matcher: Matcher, event: MessageEvent, msg: Message = CommandArg()):
        try:
            args = shlex.split(msg.extract_plain_text().strip())
        except:
            args = []
        await handle_minesweeper(matcher, event, argv + args)


shortcut("扫雷", ["--row", "8", "--col", "8", "--num", "10"], rule=to_me())
shortcut("扫雷初级", ["--row", "8", "--col", "8", "--num", "10"], rule=to_me())
shortcut("扫雷中级", ["--row", "16", "--col", "16", "--num", "40"], rule=to_me())
shortcut("扫雷高级", ["--row", "16", "--col", "30", "--num", "99"], rule=to_me())
shortcut("挖开", ["--open"], aliases={"open"}, rule=game_running)
shortcut("标记", ["--mark"], aliases={"mark"}, rule=game_running)
shortcut("查看游戏", ["--show"], aliases={"查看游戏盘", "显示游戏", "显示游戏盘"}, rule=game_running)
shortcut("结束", ["--stop"], aliases={"停", "停止游戏", "结束游戏"}, rule=game_running)


async def stop_game(matcher: Matcher, cid: str):
    timers.pop(cid, None)
    if games.get(cid, None):
        games.pop(cid)
        await matcher.finish("扫雷超时，游戏结束")


def set_timeout(matcher: Matcher, cid: str, timeout: float = 600):
    timer = timers.get(cid, None)
    if timer:
        timer.cancel()
    loop = asyncio.get_running_loop()
    timer = loop.call_later(
        timeout, lambda: asyncio.ensure_future(stop_game(matcher, cid))
    )
    timers[cid] = timer


async def handle_minesweeper(matcher: Matcher, event: MessageEvent, argv: List[str]):
    async def send(
        message: Optional[str] = None, image: Optional[BytesIO] = None
    ) -> NoReturn:
        if not (message or image):
            await matcher.finish()
        msg = Message()
        if message:
            msg.append(message)
        if image:
            msg.append(MessageSegment.image(image))
        await matcher.finish(msg)

    try:
        args = parser.parse_args(argv)
    except ParserExit as e:
        if e.status == 0:
            await send(__usage__)
        await send()

    help_msg = "使用 “挖开”+位置 挖开方块，使用 “标记”+位置 标记方块，可同时加多个位置，如：“挖开 A1 B2”"

    options = Options(**vars(args))

    cid = get_cid(event)
    if not games.get(cid, None):
        if options.open or options.mark or options.show or options.stop:
            await send("没有正在进行的游戏")

        if options.row < 8 or options.row > 24:
            await send("行数应在8~24之间")

        if options.col < 8 or options.col > 30:
            await send("列数应在8~30之间")

        if options.num < 10 or options.num > options.row * options.col:
            await send("地雷数应不少于10且不多于行数*列数")

        if options.skin not in skin_list:
            await send("支持的皮肤：" + ", ".join(skin_list))

        game = MineSweeper(options.row, options.col, options.num, options.skin)
        games[cid] = game
        set_timeout(matcher, cid)

        await send(help_msg, game.draw())

    if options.stop:
        game = games.pop(cid)
        await send("游戏已结束")

    game = games[cid]

    if options.show:
        await send(image=game.draw())

    open_positions = options.open
    mark_positions = options.mark
    if not (open_positions or mark_positions):
        await send(help_msg)

    async def check_position(position: str) -> Union[Tuple[int, int], NoReturn]:
        match_obj = re.match(r"^([a-z])(\d+)$", position, re.IGNORECASE)
        if not match_obj:
            await send("请发送 字母+数字 组成的位置，如“A1”")
        x = (ord(match_obj.group(1).lower()) - ord("a")) % 32
        y = int(match_obj.group(2)) - 1
        return x, y

    async def check_result():
        if game.end:
            msg = ""
            if game.state == GameState.WIN:
                msg = "恭喜你获得游戏胜利！"
            elif game.state == GameState.FAIL:
                msg = "很遗憾，游戏失败"
            await send(msg, image=game.draw())

    for position in open_positions:
        x, y = await check_position(position)
        res = game.open(x, y)
        if res:
            await send(res)
        await check_result()

    for position in mark_positions:
        x, y = await check_position(position)
        res = game.mark(x, y)
        if res:
            await send(res)
        await check_result()