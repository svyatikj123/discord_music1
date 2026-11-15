import discord
from discord.ext import commands
import asyncio
import os
import shutil
import tempfile
import yt_dlp
import logging
import sys, importlib.metadata
print("PYTHON:", sys.executable)
print("discord.py:", importlib.metadata.version("discord.py"))
print("davey:", importlib.metadata.version("davey"))
# ==========================================
# НАСТРОЙКИ БОТА
# ==========================================

token = os.getenv("") or ''

if not token:
    raise RuntimeError(
        "Не найден DISCORD_TOKEN! Установи переменную окружения."
    )

prefix = "!"

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix=prefix,
    intents=intents
)

# ==========================================
# НАСТРОЙКИ FFMPEG
# ==========================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FFMPEG_PATH = os.path.join(
    BASE_DIR, "bin", "ffmpeg.exe"
)

FFPROBE_PATH = os.path.join(
    BASE_DIR, "bin", "ffprobe.exe"
)

if not os.path.isfile(FFMPEG_PATH):
    raise FileNotFoundError(
        f"Не найден FFmpeg: {FFMPEG_PATH}"
    )

FFMPEG_OPTIONS = {
    "executable": FFMPEG_PATH,
    "before_options": "-nostdin",
    "options": "-vn"
}

# ==========================================
# YT-DLP
# ==========================================

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "ffmpeg_location": FFMPEG_PATH,
    "overwrites": True,
    "continuedl": False
}


def download_audio(url):

    temp_dir = tempfile.mkdtemp(
        prefix="discord_music_"
    )

    options = YDL_OPTIONS.copy()

    options["outtmpl"] = os.path.join(
        temp_dir,
        "%(id)s.%(ext)s"
    )

    try:
        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            if not info:
                raise RuntimeError(
                    "Не удалось получить информацию о треке."
                )

            if info.get("entries"):
                info = next(
                    (
                        entry
                        for entry in info["entries"]
                        if entry
                    ),
                    None
                )

            if not info:
                raise RuntimeError(
                    "Не удалось найти аудиозапись."
                )

            title = info.get(
                "title",
                "Неизвестный трек"
            )

            downloaded_files = []

            for item in info.get("requested_downloads", []):
                filepath = item.get("filepath")

                if filepath and os.path.isfile(filepath):
                    downloaded_files.append(filepath)

            if not downloaded_files:

                filepath = info.get("filepath")

                if filepath and os.path.isfile(filepath):
                    downloaded_files.append(filepath)

            if not downloaded_files:

                downloaded_files = [
                    os.path.join(temp_dir, name)
                    for name in os.listdir(temp_dir)
                    if os.path.isfile(
                        os.path.join(temp_dir, name)
                    )
                    and not name.endswith(".part")
                ]

            if not downloaded_files:
                raise RuntimeError(
                    "yt-dlp не создал аудиофайл."
                )

            audio_file = downloaded_files[0]

            if os.path.getsize(audio_file) == 0:
                raise RuntimeError(
                    "Скачанный аудиофайл пустой."
                )

            return audio_file, title, temp_dir

    except Exception:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )
        raise


# ==========================================
# МУЗЫКАЛЬНАЯ ОЧЕРЕДЬ
# ==========================================

queues = {}
playing = set()


def get_queue(guild_id):

    if guild_id not in queues:
        queues[guild_id] = []

    return queues[guild_id]


# ==========================================
# ДИАГНОСТИКА ГОЛОСОВОГО КАНАЛА
# ==========================================

def voice_check(ctx):

    me = ctx.guild.me
    voice_client = ctx.voice_client

    if not voice_client:
        print("Бот не подключён.")
        return

    channel = voice_client.channel
    perms = channel.permissions_for(me)

    print("=== VOICE CHECK ===")
    print("Канал:", channel.name)
    print("Connect:", perms.connect)
    print("Speak:", perms.speak)
    print("View channel:", perms.view_channel)
    print(
        "Server mute:",
        me.voice.mute if me.voice else None
    )
    print(
        "Self mute:",
        me.voice.self_mute if me.voice else None
    )
    print(
        "Подключён:",
        voice_client.is_connected()
    )
    print("===================")


# ==========================================
# ЗАВЕРШЕНИЕ ТРЕКА
# ==========================================

async def finish_track(ctx, temp_dir):

    if temp_dir:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

    await play_next(ctx)


# ==========================================
# СЛЕДУЮЩИЙ ТРЕК
# ==========================================

async def play_next(ctx):

    guild_id = ctx.guild.id

    while True:

        voice_client = ctx.voice_client

        if not voice_client or not voice_client.is_connected():
            playing.discard(guild_id)
            return

        queue = get_queue(guild_id)

        if not queue:
            playing.discard(guild_id)

            await ctx.send(
                "📭 Очередь закончилась."
            )
            return

        page_url = queue.pop(0)

        temp_dir = None

        try:

            await ctx.send(
                "⏳ Загружаю аудио..."
            )

            audio_file, title, temp_dir = await asyncio.to_thread(
                download_audio,
                page_url
            )

            if not os.path.isfile(audio_file):
                raise RuntimeError(
                    "Аудиофайл не найден."
                )

            if not ctx.voice_client or not ctx.voice_client.is_connected():
                shutil.rmtree(temp_dir, ignore_errors=True)
                playing.discard(guild_id)
                return

            print("=== АУДИО ===")
            print("Название:", title)
            print("Файл:", audio_file)
            print("Размер:", os.path.getsize(audio_file))
            print("FFmpeg:", FFMPEG_PATH)
            print("============")

            audio_source = discord.FFmpegPCMAudio(
                audio_file,
                **FFMPEG_OPTIONS
            )

            original_read = audio_source.read

            audio_stats = {
                "packets": 0,
                "bytes": 0
            }

            def counted_read():

                data = original_read()

                if data:
                    audio_stats["packets"] += 1
                    audio_stats["bytes"] += len(data)

                return data

            audio_source.read = counted_read

            source = discord.PCMVolumeTransformer(
                audio_source,
                volume=0.5
            )

            def after_play(error):

                print("=== ДИАГНОСТИКА DISCORD ===")
                print("Ошибка:", repr(error))

                if ctx.voice_client:

                    print(
                        "Соединение активно:",
                        ctx.voice_client.is_connected()
                    )

                    print(
                        "Воспроизведение:",
                        ctx.voice_client.is_playing()
                    )

                me = ctx.guild.me

                print(
                    "Server mute:",
                    me.voice.mute if me.voice else None
                )

                print(
                    "Self mute:",
                    me.voice.self_mute if me.voice else None
                )

                print(
                    "Аудиопакетов:",
                    audio_stats["packets"]
                )

                print(
                    "Передано байт:",
                    audio_stats["bytes"]
                )

                print("==========================")

                if bot.is_closed():
                    return

                future = asyncio.run_coroutine_threadsafe(
                    finish_track(ctx, temp_dir),
                    bot.loop
                )

                def report_error(f):
                    try:
                        f.result()
                    except Exception as e:
                        print("Ошибка завершения трека:", repr(e))

                future.add_done_callback(report_error)

            ctx.voice_client.play(
                source,
                after=after_play
            )

            await ctx.send(
                f"🎶 Сейчас играет: **{title}**"
            )

            return

        except Exception as e:

            print(
                "Ошибка воспроизведения:",
                repr(e)
            )

            if temp_dir:
                shutil.rmtree(
                    temp_dir,
                    ignore_errors=True
                )

            await ctx.send(
                f"❌ Ошибка воспроизведения: {e}"
            )

            continue


# ==========================================
# ПОДКЛЮЧЕНИЕ К ГОЛОСОВОМУ КАНАЛУ
# ==========================================

@bot.command(name="join1")
async def join(ctx):

    if not ctx.author.voice:
        return await ctx.send(
            "❌ Сначала зайди в голосовой канал."
        )

    channel = ctx.author.voice.channel

    if ctx.voice_client is None:

        await channel.connect()

    else:

        await ctx.voice_client.move_to(channel)

    voice_check(ctx)

    await ctx.send(
        f"✅ Подключился к каналу **{channel.name}**"
    )


# ==========================================
# ВОСПРОИЗВЕДЕНИЕ МУЗЫКИ
# ==========================================

@bot.command(name="play1")
async def play(ctx, *, query: str):

    if not query.startswith(("http://", "https://")):
        return await ctx.send(
            "❌ Вставь ссылку на SoundCloud или прямую ссылку на аудио."
        )

    if not ctx.author.voice:
        return await ctx.send(
            "❌ Сначала зайди в голосовой канал."
        )

    if not ctx.voice_client:
        await ctx.invoke(join)

    if not ctx.voice_client:
        return

    # Проверка разрешений и отключения звука
    voice_check(ctx)

    guild_id = ctx.guild.id

    queue = get_queue(guild_id)
    queue.append(query)

    await ctx.send(
        "➕ Ссылка добавлена в очередь."
    )

    if (
        not ctx.voice_client.is_playing()
        and not ctx.voice_client.is_paused()
        and guild_id not in playing
    ):

        playing.add(guild_id)

        await play_next(ctx)


# ==========================================
# ПАУЗА
# ==========================================

@bot.command(name="pause")
async def pause(ctx):

    if ctx.voice_client and ctx.voice_client.is_playing():

        ctx.voice_client.pause()

        await ctx.send(
            "⏸️ Музыка на паузе."
        )

    else:

        await ctx.send(
            "❌ Сейчас ничего не играет."
        )


# ==========================================
# ПРОДОЛЖИТЬ
# ==========================================

@bot.command(name="resume")
async def resume(ctx):

    if ctx.voice_client and ctx.voice_client.is_paused():

        ctx.voice_client.resume()

        await ctx.send(
            "▶️ Продолжаю воспроизведение."
        )

    else:

        await ctx.send(
            "❌ Воспроизведение не приостановлено."
        )


# ==========================================
# ПРОПУСТИТЬ
# ==========================================

@bot.command(name="skip")
async def skip(ctx):

    if ctx.voice_client and (
        ctx.voice_client.is_playing()
        or ctx.voice_client.is_paused()
    ):

        ctx.voice_client.stop()

        await ctx.send(
            "⏭️ Трек пропущен."
        )

    else:

        await ctx.send(
            "❌ Нечего пропускать."
        )


# ==========================================
# ОСТАНОВИТЬ МУЗЫКУ
# ==========================================

@bot.command(name="stop")
async def stop(ctx):

    if ctx.voice_client:

        get_queue(ctx.guild.id).clear()

        if (
            ctx.voice_client.is_playing()
            or ctx.voice_client.is_paused()
        ):

            ctx.voice_client.stop()

        else:

            playing.discard(ctx.guild.id)

        await ctx.send(
            "⏹️ Воспроизведение остановлено, очередь очищена."
        )

    else:

        await ctx.send(
            "❌ Бот не подключён к голосовому каналу."
        )


# ==========================================
# ОТКЛЮЧЕНИЕ
# ==========================================

@bot.command(name="leave")
async def leave(ctx):

    if ctx.voice_client:

        queues.pop(ctx.guild.id, None)

        ctx.voice_client.stop()

        await ctx.voice_client.disconnect()

        playing.discard(ctx.guild.id)

        await ctx.send(
            "👋 Отключился от голосового канала."
        )

    else:

        await ctx.send(
            "❌ Бот не находится в голосовом канале."
        )


# ==========================================
# ПОКАЗАТЬ ОЧЕРЕДЬ
# ==========================================

@bot.command(name="queue")
async def queue_command(ctx):

    queue = get_queue(ctx.guild.id)

    if not queue:
        return await ctx.send(
            "📭 Очередь пуста."
        )

    msg = "\n".join(
        f"{i + 1}. {url}"
        for i, url in enumerate(queue)
    )

    await ctx.send(
        f"**🎵 Очередь музыки:**\n{msg}"
    )


# ==========================================
# СТАРЫЕ КОМАНДЫ
# ==========================================

@bot.command()
async def ser(ctx):

    for i in range(10):
        await ctx.reply("1")


async def mute_member(channels, member, mute):

    for channel in channels:

        for voice_member in channel.members:

            if member == voice_member:

                await member.edit(mute=mute)
                return


@bot.command(name="unmute")
async def unmute(ctx, member: discord.Member = None):

    if member:

        muted_role = discord.utils.get(
            ctx.guild.roles,
            name="Muted"
        )

        if muted_role:
            await member.remove_roles(muted_role)

        try:
            await member.send(
                "You have been unmuted"
            )
        except discord.Forbidden:
            pass

        await mute_member(
            ctx.guild.voice_channels,
            member,
            False
        )

    else:

        await ctx.send(
            "Didn't work, try using the command properly"
        )


@bot.command(name="mute")
async def mute(ctx, member: discord.Member):

    await member.edit(mute=True)


@bot.command(name="muteall")
async def muteall(ctx):

    for channel in ctx.guild.voice_channels:

        for member in channel.members:

            await member.edit(mute=True)


@bot.command(name="unmuteall")
async def unmuteall(ctx):

    for channel in ctx.guild.voice_channels:

        for member in channel.members:

            await member.edit(mute=False)


@bot.command(name="uskick")
async def uskik(ctx, member: discord.Member):

    await member.edit(voice_channel=None)


@bot.command(name="usakick")
async def usakick(ctx):

    for channel in ctx.guild.voice_channels:

        for member in channel.members:

            await member.edit(voice_channel=None)


@bot.command(name="allmuteserv")
async def allmuteserv(
    ctx,
    member: discord.Member = None,
    amount: str = None,
    *,
    reason=None
):

    if member:
        await member.edit(mute=True)


# ==========================================
# ОБРАБОТКА ОШИБОК
# ==========================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):

        await ctx.send(
            "❌ Не хватает аргументов команды."
        )

        return

    print(
        "Ошибка команды:",
        repr(error)
    )

    await ctx.send(
        f"❌ Ошибка: {error}"
    )


# ==========================================
# ЗАПУСК БОТА
# ==========================================

@bot.event
async def on_ready():

    print(f"Бот запущен: {bot.user}")
    print("Музыкальная система готова.")

@bot.command(name="testvoice")
async def testvoice(ctx):
    if not ctx.author.voice:
        return await ctx.send("Сначала зайди в голосовой канал.")

    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()

    vc = ctx.voice_client

    if vc.is_playing() or vc.is_paused():
        vc.stop()

    test_file = os.path.join(BASE_DIR, "test.mp3")

    if not os.path.isfile(test_file):
        return await ctx.send(f"Файл не найден: {test_file}")

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(test_file, **FFMPEG_OPTIONS),
        volume=0.5
    )

    vc.play(source)
    await ctx.send("🔊 Тестовый звук запущен!")

@bot.command(name="voiceinfo")
async def voiceinfo(ctx):
    vc = ctx.voice_client

    if not vc:
        return await ctx.send("Бот не подключён к голосовому каналу.")

    me = ctx.guild.me
    perms = vc.channel.permissions_for(me)

    await ctx.send(
        f"Подключён: {vc.is_connected()}\n"
        f"Играет: {vc.is_playing()}\n"
        f"Приостановлен: {vc.is_paused()}\n"
        f"Connect: {perms.connect}\n"
        f"Speak: {perms.speak}\n"
        f"Server mute: {me.voice.mute if me.voice else None}\n"
        f"Self mute: {me.voice.self_mute if me.voice else None}"
    )

@bot.command(name="voicetest")
async def voicetest(ctx):
    vc = ctx.voice_client

    if not vc:
        return await ctx.send("Бот не подключён.")

    await ctx.send(
        f"Connected: {vc.is_connected()}\n"
        f"Playing: {vc.is_playing()}\n"
        f"Latency: {bot.latency:.3f}\n"
        f"Voice protocol: {type(vc._connection).__name__}"
    )

bot.run(token, log_level=logging.DEBUG)
