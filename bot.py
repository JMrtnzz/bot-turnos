import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from datetime import datetime, timezone, timedelta

# =========================
# CONFIGURACIÓN
# =========================
import os
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1277376211005214921   # opcional, para sincronizar más rápido los comandos
LOG_CHANNEL_ID = 1487552350456381792  # canal donde se enviarán los turnos

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

# Historial de turnos cerrados (para poder sumar horas)
cursor.execute("""
CREATE TABLE IF NOT EXISTS historial_fichajes (
    shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL
)
""")
conn.commit()

# =========================
# INTENTS
# =========================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

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

def add_shift_history(user_id: int, start_time: str, end_time: str, duration_seconds: int):
    cursor.execute("""
        INSERT INTO historial_fichajes (user_id, start_time, end_time, duration_seconds)
        VALUES (?, ?, ?, ?)
    """, (user_id, start_time, end_time, duration_seconds))
    conn.commit()

def format_duration(seconds: int):
    horas = seconds // 3600
    minutos = (seconds % 3600) // 60
    segundos = seconds % 60
    return horas, minutos, segundos

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

        # Guardar historial antes de borrar el turno abierto
        add_shift_history(
            user_id=user_id,
            start_time=existing[0],
            end_time=now.isoformat(),
            duration_seconds=total_seconds,
        )

        # borrar turno abierto
        close_shift(user_id)

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
# SLASH COMMAND ADMIN: HORAS
# =========================
@bot.tree.command(
    name="admin_horas",
    description="Muestra total_horas y horas_semana (últimos 7 días). Solo administradores.",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def admin_horas(interaction: discord.Interaction, max_usuarios: app_commands.Range[int, 1, 50] = 25):
    now_dt = datetime.now(timezone.utc)
    cutoff_dt = (now_dt - timedelta(days=7)).replace(microsecond=0)

    # SQLite: sumas por usuario (total, todo el historial)
    cursor.execute("""
        SELECT user_id, SUM(duration_seconds) as total_seconds
        FROM historial_fichajes
        GROUP BY user_id
    """)
    totals_rows = cursor.fetchall()
    total_seconds_by_user = {int(r[0]): int(r[1]) for r in totals_rows if r[1] is not None}

    # Última semana: sumamos SOLO la superposición de cada turno con [cutoff, now]
    cursor.execute("""
        SELECT user_id, start_time, end_time
        FROM historial_fichajes
        WHERE end_time > ?
    """, (cutoff_dt.isoformat(),))
    week_rows = cursor.fetchall()

    weekly_seconds_by_user = {}
    for user_id, start_time_str, end_time_str in week_rows:
        start_dt = datetime.fromisoformat(start_time_str)
        end_dt = datetime.fromisoformat(end_time_str)

        overlap_start = max(start_dt, cutoff_dt)
        overlap_end = min(end_dt, now_dt)
        overlap_seconds = int((overlap_end - overlap_start).total_seconds())
        if overlap_seconds > 0:
            weekly_seconds_by_user[int(user_id)] = weekly_seconds_by_user.get(int(user_id), 0) + overlap_seconds

    # Unimos usuarios presentes en total y/o semana
    user_ids = set(total_seconds_by_user.keys()) | set(weekly_seconds_by_user.keys())
    if not user_ids:
        em = discord.Embed(
            title="Sin datos",
            description="Aún no hay turnos cerrados en el historial.",
            color=COLOR_AVISO,
        )
        await interaction.response.send_message(embed=em, ephemeral=True)
        return

    def to_hm(seconds: int):
        h, m, _ = format_duration(max(0, seconds))
        return h, m

    # Ordenamos por horas de la semana (desc)
    sorted_users = sorted(
        user_ids,
        key=lambda uid: weekly_seconds_by_user.get(uid, 0),
        reverse=True,
    )[:max_usuarios]

    embed = discord.Embed(
        title="📊 Horas (Admin)",
        description="`total_horas` = todo el historial | `horas_semana` = últimos 7 días",
        color=COLOR_PANEL,
    )

    for uid in sorted_users:
        total_seconds = total_seconds_by_user.get(uid, 0)
        week_seconds = weekly_seconds_by_user.get(uid, 0)

        total_h, total_m = to_hm(total_seconds)
        week_h, week_m = to_hm(week_seconds)

        member = interaction.guild.get_member(uid) if interaction.guild else None
        name = member.display_name if member else f"ID {uid}"

        embed.add_field(
            name=name,
            value=f"Total: **{total_h}h {total_m}m**\nSemana: **{week_h}h {week_m}m**",
            inline=False,
        )

    remaining = len(user_ids) - len(sorted_users)
    if remaining > 0:
        embed.set_footer(text=f"Mostrando {len(sorted_users)}/{len(user_ids)} usuarios (faltan {remaining}).")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# EJECUCIÓN
# =========================
bot.run(TOKEN)