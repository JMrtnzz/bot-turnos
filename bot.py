import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from datetime import datetime, timezone

# =========================
# CONFIGURACIÓN
# =========================
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
# Sin prefijo: solo slash commands (evita el aviso de Message Content Intent en hosts como Render)
bot = commands.Bot(command_prefix=[], intents=intents)

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
class ExitView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=900)  # Discord limita la vida de componentes a unos minutos
        self.user_id = user_id

    @discord.ui.button(label="Salir", style=discord.ButtonStyle.danger)
    async def salir(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            em = discord.Embed(
                title="⛔ No es tu turno",
                description="Este botón es solo para la persona que inició el fichaje.",
                color=COLOR_ALERTA,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await interaction.response.send_message(embed=em, ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        existing = get_open_shift(self.user_id)
        if not existing:
            em = discord.Embed(
                title="🟡 No hay turno abierto",
                description="No se encontró un turno activo para ti.",
                color=COLOR_AVISO,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await interaction.response.edit_message(embed=em, view=None)
            return

        start_time = datetime.fromisoformat(existing[0])
        duration = now - start_time
        total_seconds = int(duration.total_seconds())
        horas, minutos, segundos = format_duration(total_seconds)

        # Cerrar turno antes de enviar/registrar para mantener consistencia
        close_shift(self.user_id)

        # buscar canal de logs
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            em = discord.Embed(
                title="❌ Canal de registro no encontrado",
                description=(
                    "No se pudo localizar el canal de logs.\n"
                    "Revisa que **LOG_CHANNEL_ID** sea correcto."
                ),
                color=COLOR_ALERTA,
            )
            await interaction.response.edit_message(embed=em, view=None)
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

        # Quitamos el botón del mensaje efímero
        await interaction.response.edit_message(embed=em_done, view=None)


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
                    "Usa el botón **Salir** que aparece en este mensaje para cerrarla."
                ),
                color=COLOR_AVISO,
            )
            em.set_footer(text="Solo tú ves este mensaje")
            await interaction.response.send_message(
                embed=em,
                ephemeral=True,
                view=ExitView(user_id),
            )
            return

        open_shift(user_id, now.isoformat())

        em = discord.Embed(
            title="🟢 Turno iniciado",
            description=(
                f"**Entrada registrada:** `{now.strftime('%H:%M:%S')}` **UTC**\n\n"
                "Cuando termines, pulsa **Salir** en este mensaje para guardar el registro."
            ),
            color=COLOR_OK,
        )
        em.set_footer(text="¡Buen turno!")
        await interaction.response.send_message(
            embed=em,
            ephemeral=True,
            view=ExitView(user_id),
        )

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
            "Tras iniciarlo, te aparecerá un mensaje temporal con el botón **Salir**.\n\n"
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
# EJECUCIÓN
# =========================
def _start_render_health_port():
    """Render (Web Service) inyecta PORT y comprueba que haya un socket abierto."""
    raw = os.environ.get("PORT")
    if not raw:
        return
    port = int(raw)

    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    def _serve():
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        server.serve_forever()

    threading.Thread(target=_serve, daemon=True, name="render-health").start()
    print(f"Health check HTTP en 0.0.0.0:{port} (Render)")


_start_render_health_port()
bot.run(TOKEN)