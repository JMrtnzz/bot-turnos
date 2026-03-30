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
            await interaction.response.send_message(
                "Ya tienes un turno iniciado. Primero debes pulsar **Salir**.",
                ephemeral=True
            )
            return

        open_shift(user_id, now.isoformat())

        await interaction.response.send_message(
            f"✅ Turno iniciado a las **{now.strftime('%H:%M:%S UTC')}**",
            ephemeral=True
        )

    @discord.ui.button(label="Salir", style=discord.ButtonStyle.danger, custom_id="fichaje_salir")
    async def salir(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        existing = get_open_shift(user_id)
        if not existing:
            await interaction.response.send_message(
                "No tienes ningún turno iniciado.",
                ephemeral=True
            )
            return

        start_time = datetime.fromisoformat(existing[0])
        duration = now - start_time
        total_seconds = int(duration.total_seconds())

        horas, minutos, segundos = format_duration(total_seconds)

        # borrar turno abierto
        close_shift(user_id)

        # buscar canal de logs
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            await interaction.response.send_message(
                "No encontré el canal de logs. Revisa el LOG_CHANNEL_ID.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Ficha de horas",
            color=discord.Color.blue(),
            timestamp=now
        )
        embed.add_field(name="Usuario", value=f"{interaction.user.mention} (`{interaction.user}`)", inline=False)
        embed.add_field(name="Hora de entrada", value=start_time.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
        embed.add_field(name="Hora de salida", value=now.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
        embed.add_field(
            name="Tiempo total",
            value=f"**{horas}h {minutos}m {segundos}s**",
            inline=False
        )

        await log_channel.send(embed=embed)

        await interaction.response.send_message(
            f"✅ Turno cerrado. Total trabajado: **{horas}h {minutos}m {segundos}s**",
            ephemeral=True
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
        title="Control de Turnos",
        description="Pulsa **Entrar** para iniciar tu turno y **Salir** para finalizarlo.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=FichajeView())

# =========================
# EJECUCIÓN
# =========================
bot.run(TOKEN)