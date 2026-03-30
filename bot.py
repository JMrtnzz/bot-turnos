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

def open_shift(user_id: int, start_time: str) -> int:
    """
    Abre turno si no existe.
    Devuelve 1 si se insertó, 0 si ya había un turno abierto.
    """
    cursor.execute(
        "INSERT OR IGNORE INTO fichajes (user_id, start_time) VALUES (?, ?)",
        (user_id, start_time),
    )
    inserted = cursor.rowcount
    conn.commit()
    return inserted

def close_shift(user_id: int) -> int:
    cursor.execute("DELETE FROM fichajes WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    return deleted

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

    async def _safe_respond(self, interaction: discord.Interaction, *, embed, ephemeral: bool):
        """
        Evita que el bot spamee trazas cuando la interacción ya no es válida
        (p.ej. por doble ejecución o lentitud en producción).
        """
        try:
            if getattr(interaction.response, "is_done", lambda: False)():
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        except (discord.NotFound, discord.InteractionResponded, AttributeError):
            # NotFound (10062): Unknown interaction (token expirado o ya usado)
            # InteractionResponded: ya respondida (según versión de discord.py)
            return

    @discord.ui.button(label="Entrar", style=discord.ButtonStyle.success, custom_id="fichaje_entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        inserted = open_shift(user_id, now.isoformat())
        if inserted == 0:
            em = discord.Embed(
                title="⚠️ Ya tienes un turno activo",
                description=(
                    "Tienes una sesión **en curso**.\n\n"
                    "Pulsa **Salir** para cerrarla antes de iniciar otra."
                ),
                color=COLOR_AVISO,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await self._safe_respond(interaction, embed=em, ephemeral=True)
            return

        em = discord.Embed(
            title="🟢 Turno iniciado",
            description=(
                f"**Entrada registrada:** `{now.strftime('%H:%M:%S')}` **UTC**\n\n"
                "Cuando termines, pulsa **Salir** para guardar el registro."
            ),
            color=COLOR_OK,
        )
        em.set_footer(text="¡Buen turno!")
        await self._safe_respond(interaction, embed=em, ephemeral=True)

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
            await self._safe_respond(interaction, embed=em, ephemeral=True)
            return

        start_time = datetime.fromisoformat(existing[0])
        duration = now - start_time
        total_seconds = int(duration.total_seconds())

        horas, minutos, segundos = format_duration(total_seconds)

        # borrar turno abierto (si ya lo cerró otro callback casi a la vez,
        # no enviamos el log dos veces).
        deleted = close_shift(user_id)
        if deleted == 0:
            # Compatibilidad: si `is_done()` no existe, asumimos que no.
            if not getattr(interaction.response, "is_done", lambda: False)():
                em = discord.Embed(
                    title="🟡 Turno ya cerrado",
                    description=(
                        "El turno ya fue finalizado por otra acción.\n\n"
                        "Si necesitas, pulsa **Entrar** para iniciar de nuevo."
                    ),
                    color=COLOR_AVISO,
                )
                em.set_footer(text="Solo tú ves este mensaje")
                await self._safe_respond(interaction, embed=em, ephemeral=True)
            return

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
            await self._safe_respond(interaction, embed=em, ephemeral=True)
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
        await self._safe_respond(interaction, embed=em_done, ephemeral=True)

# Reutilizamos una sola instancia para evitar que el mismo `custom_id` quede
# registrado dos veces (y se ejecute el callback duplicado).
fichaje_view = FichajeView()
view_added = False

# =========================
# EVENTOS
# =========================
@bot.event
async def on_ready():
    global view_added
    # Registrar la vista persistente solo una vez (on_ready puede repetirse
    # si hay reconexiones).
    if not view_added:
        bot.add_view(fichaje_view)
        view_added = True

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
    await interaction.response.send_message(embed=embed, view=fichaje_view)

# =========================
# EJECUCIÓN
# =========================
bot.run(TOKEN)