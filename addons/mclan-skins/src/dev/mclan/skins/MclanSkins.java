package dev.mclan.skins;

import org.bukkit.plugin.java.JavaPlugin;
import org.geysermc.floodgate.api.FloodgateApi;
import org.geysermc.floodgate.api.event.skin.SkinApplyEvent;

/**
 * Floodgate aplica a cada jugador Bedrock la conversión Java de su skin (API de GeyserMC). Si existe,
 * Geyser la manda también a los clientes Bedrock y la skin original, con su geometría, se pierde.
 * Cancelando esa aplicación, Java ve la skin por defecto y Geyser recurre a la skin Bedrock original.
 */
public final class MclanSkins extends JavaPlugin {
    @Override
    public void onEnable() {
        saveDefaultConfig();
        String mode = getConfig().getString("java-skin", "default");
        if ("converted".equals(mode)) {
            getLogger().info("java-skin: converted. Floodgate applies the converted skin as usual.");
            return;
        }
        if (!"default".equals(mode)) {
            getLogger().warning("Unknown java-skin '" + mode + "'; using default.");
        }
        FloodgateApi.getInstance().getEventBus().subscribe(SkinApplyEvent.class, event -> event.setCancelled(true));
        getLogger().info("java-skin: default. Java sees the default skin; Bedrock sees the original Bedrock skin.");
    }
}
