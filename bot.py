import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from datetime import datetime, timezone

# =========================
# CONFIGURACIÓN
# =========================
import os
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1277376211005214921   # opcional, para sincronizar más rápido los comandos
LOG_CHANNEL_ID = 1488178271966466179  # canal donde se enviarán los turnos

# =========================
# BASE DE DATOS
# =========================
conn = sqlite3.connect("turnos.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS fichajes (
    user_id INTEGER PRIMARY KEY,
    start_time TEXT
)
""")
conn.commit()

# Historial de turnos cerrados (para reportes y totales)
cursor.execute("""
CREATE TABLE IF NOT EXISTS registros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    seconds INTEGER NOT NULL
)
""")
conn.commit()

# =========================
# INTENTS
# =========================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# SERVIDOR WEB (Render "Web Service")
# =========================
# Render exige que el proceso se "bind-ee" a al menos un puerto.
# Como este bot no necesita servir páginas, exponemos un endpoint mínimo de salud.
async def http_health_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        # Leemos hasta el final de headers HTTP para no bloquear indefinidamente.
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    except Exception:
        # Si el cliente se corta o no envía headers completos, igual devolvemos OK.
        pass

    body = b"OK"
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: 2\r\n"
        b"\r\n"
        + body
    )
    writer.write(response)
    await writer.drain()
    writer.close()
    # wait_closed() puede fallar si el cliente cierra rápido
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def start_web_server():
    port = int(os.getenv("PORT", "8080"))
    server = await asyncio.start_server(http_health_handler, host="0.0.0.0", port=port)
    print(f"Web server escuchando en 0.0.0.0:{port}")
    return server


# contextlib solo se usa para suprimir errores al cerrar sockets
import contextlib


# =========================
# FUNCIONES AUXILIARES
# =========================
def get_open_shift(user_id: int):
    cursor.execute("SELECT start_time FROM fichajes WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def open_shift(user_id: int, start_time: str):
    cursor.execute("""
        INSERT OR REPLACE INTO fichajes (user_id, start_time)
        VALUES (?, ?)
    """, (user_id, start_time))
    conn.commit()

def close_shift(user_id: int):
    cursor.execute("DELETE FROM fichajes WHERE user_id = ?", (user_id,))
    conn.commit()

def save_shift_record(user_id: int, start_time: str, end_time: str, seconds: int):
    cursor.execute(
        """
        INSERT INTO registros (user_id, start_time, end_time, seconds)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, start_time, end_time, seconds),
    )
    conn.commit()

def format_duration(seconds: int):
    horas = seconds // 3600
    minutos = (seconds % 3600) // 60
    segundos = seconds % 60
    return horas, minutos, segundos

def get_totals_by_user(include_open_shifts: bool = True):
    cursor.execute("SELECT user_id, COALESCE(SUM(seconds), 0) FROM registros GROUP BY user_id")
    totals = {int(user_id): int(total_seconds) for (user_id, total_seconds) in cursor.fetchall()}

    if include_open_shifts:
        now = datetime.now(timezone.utc)
        cursor.execute("SELECT user_id, start_time FROM fichajes")
        for user_id, start_time in cursor.fetchall():
            try:
                start_dt = datetime.fromisoformat(start_time)
                running = int((now - start_dt).total_seconds())
                totals[int(user_id)] = totals.get(int(user_id), 0) + max(0, running)
            except Exception:
                # Si algún registro estuviera corrupto, no rompe el reporte.
                totals[int(user_id)] = totals.get(int(user_id), 0)

    return totals

def reset_registros():
    cursor.execute("SELECT COUNT(*) FROM registros")
    (count_before,) = cursor.fetchone()
    cursor.execute("DELETE FROM registros")
    conn.commit()
    return int(count_before)

# Colores para embeds (coherentes en todo el bot)
COLOR_PANEL = discord.Color.from_rgb(88, 101, 242)   # blurple
COLOR_OK = discord.Color.from_rgb(46, 204, 113)      # verde éxito
COLOR_AVISO = discord.Color.from_rgb(241, 196, 15)   # ámbar
COLOR_ALERTA = discord.Color.from_rgb(231, 76, 60)   # rojo suave
COLOR_LOG = discord.Color.from_rgb(52, 152, 219)   # azul registro

# =========================
# VIEW CON BOTONES
# =========================
class FichajeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # para que no expire

    @discord.ui.button(label="Entrar", style=discord.ButtonStyle.success, custom_id="fichaje_entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        existing = get_open_shift(user_id)
        if existing:
            em = discord.Embed(
                title="⚠️ Ya tienes un turno activo",
                description=(
                    "Tienes una sesión **en curso**.\n\n"
                    "Pulsa **Salir** para cerrarla antes de iniciar otra."
                ),
                color=COLOR_AVISO,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await interaction.response.send_message(embed=em, ephemeral=True)
            return

        open_shift(user_id, now.isoformat())

        em = discord.Embed(
            title="🟢 Turno iniciado",
            description=(
                f"**Entrada registrada:** `{now.strftime('%H:%M:%S')}` **UTC**\n\n"
                "Cuando termines, pulsa **Salir** para guardar el registro."
            ),
            color=COLOR_OK,
        )
        em.set_footer(text="¡Buen turno!")
        await interaction.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="Salir", style=discord.ButtonStyle.danger, custom_id="fichaje_salir")
    async def salir(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        existing = get_open_shift(user_id)
        if not existing:
            em = discord.Embed(
                title="🟡 No hay turno abierto",
                description=(
                    "No tienes ninguna sesión activa en este momento.\n\n"
                    "Pulsa **Entrar** cuando empieces a trabajar."
                ),
                color=COLOR_AVISO,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await interaction.response.send_message(embed=em, ephemeral=True)
            return

        start_time = datetime.fromisoformat(existing[0])
        duration = now - start_time
        total_seconds = int(duration.total_seconds())

        horas, minutos, segundos = format_duration(total_seconds)

        # borrar turno abierto
        close_shift(user_id)

        # guardar historial del turno cerrado
        save_shift_record(
            user_id=user_id,
            start_time=start_time.isoformat(),
            end_time=now.isoformat(),
            seconds=total_seconds,
        )

        # buscar canal de logs
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            em = discord.Embed(
                title="❌ Canal de registro no encontrado",
                description=(
                    "No se pudo localizar el canal de logs.\n"
                    "Revisa que **LOG_CHANNEL_ID** sea correcto en la configuración del bot."
                ),
                color=COLOR_ALERTA,
            )
            await interaction.response.send_message(embed=em, ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 Registro de turno",
            description=f"{interaction.user.mention} · **Turno finalizado**",
            color=COLOR_LOG,
            timestamp=now,
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(
            name="🕐 Entrada",
            value=f"`{start_time.strftime('%Y-%m-%d %H:%M:%S')}` UTC",
            inline=True,
        )
        embed.add_field(
            name="🕐 Salida",
            value=f"`{now.strftime('%Y-%m-%d %H:%M:%S')}` UTC",
            inline=True,
        )
        embed.add_field(
            name="⏱️ Tiempo total",
            value=f"**`{horas}h`** **`{minutos}m`** **`{segundos}s`**",
            inline=False,
        )
        embed.set_footer(text="Fichaje automático")

        await log_channel.send(embed=embed)

        em_done = discord.Embed(
            title="✅ Turno cerrado correctamente",
            description=(
                f"**Duración:** `{horas}h {minutos}m {segundos}s`\n\n"
                "El registro quedó guardado en el canal de **logs**."
            ),
            color=COLOR_OK,
        )
        em_done.set_footer(text="Solo tú ves este mensaje")
        await interaction.response.send_message(embed=em_done, ephemeral=True)

# =========================
# EVENTOS
# =========================
@bot.event
async def on_ready():
    # registrar la vista persistente
    bot.add_view(FichajeView())

    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"Comandos sincronizados en guild: {len(synced)}")
    except Exception as e:
        print(f"Error al sincronizar comandos: {e}")

    print(f"Bot conectado como {bot.user}")

# =========================
# SLASH COMMAND PARA CREAR EL PANEL
# =========================
@bot.tree.command(name="panel_fichaje", description="Crea el panel de fichaje", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def panel_fichaje(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⏱️ Panel de fichaje",
        description=(
            "**Registra tu jornada con los botones de abajo.**\n\n"
            "🟢 **Entrar** — Marca el **inicio** de tu turno.\n"
            "🔴 **Salir** — Marca el **fin** y guarda el tiempo en el canal de registros.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "*Cierra siempre el turno al terminar para que quede bien el registro.*"
        ),
        color=COLOR_PANEL,
    )
    embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
    footer_text = "Sistema de turnos · Las confirmaciones son privadas (solo las ves tú)"
    if interaction.guild and interaction.guild.icon:
        embed.set_footer(text=footer_text, icon_url=interaction.guild.icon.url)
    else:
        embed.set_footer(text=footer_text)
    await interaction.response.send_message(embed=embed, view=FichajeView())

# =========================
# SLASH COMMAND: TOTALES DEL REGISTRO
# =========================
@bot.tree.command(
    name="totales_turnos",
    description="Muestra el total acumulado (h/m/s) de cada usuario según el registro",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def totales_turnos(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    totals = get_totals_by_user(include_open_shifts=False)
    if not totals:
        em = discord.Embed(
            title="📊 Totales de turnos",
            description="Aún no hay registros guardados.",
            color=COLOR_AVISO,
        )
        await interaction.followup.send(embed=em, ephemeral=True)
        return

    # Ordenar por más tiempo primero
    ordered = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    lines = []
    for user_id, seconds in ordered:
        h, m, s = format_duration(int(seconds))
        lines.append(f"<@{user_id}> — **`{h}h`** **`{m}m`** **`{s}s`**")

    # Discord: límite de 4096 chars en descripción de embed
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 3500:  # margen para seguridad
            chunks.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())

    for idx, chunk in enumerate(chunks, start=1):
        em = discord.Embed(
            title="📊 Totales de turnos" if len(chunks) == 1 else f"📊 Totales de turnos ({idx}/{len(chunks)})",
            description=chunk,
            color=COLOR_PANEL,
        )
        em.set_footer(text="Solo turnos cerrados (según el registro)")
        await interaction.followup.send(embed=em, ephemeral=True)

# =========================
# SLASH COMMAND: RESET DEL REGISTRO
# =========================
@bot.tree.command(
    name="reset",
    description="Borra todos los turnos guardados en el registro (registros)",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def reset(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    deleted = reset_registros()

    em = discord.Embed(
        title="🧹 Registro reseteado",
        description=f"Se borraron **{deleted}** registros de la tabla `registros`.",
        color=COLOR_OK if deleted > 0 else COLOR_AVISO,
    )
    await interaction.followup.send(embed=em, ephemeral=True)

# =========================
# EJECUCIÓN
# =========================
if not TOKEN:
    raise RuntimeError("TOKEN no está configurado. Asegúrate de definir la variable de entorno TOKEN.")


async def main():
    # 1) Levantamos el servidor HTTP mínimo para Render.
    server = await start_web_server()
    try:
        # 2) Arrancamos el bot (no devuelve).
        await bot.start(TOKEN)
    finally:
        # Si el proceso termina, cerramos el servidor HTTP.
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())