package dev.mclan.skins;

import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.bukkit.plugin.java.JavaPlugin;
import org.geysermc.floodgate.api.FloodgateApi;
import org.geysermc.floodgate.api.event.skin.SkinApplyEvent;
import org.geysermc.geyser.api.GeyserApi;
import org.geysermc.geyser.api.connection.GeyserConnection;
import org.geysermc.geyser.api.event.EventRegistrar;
import org.geysermc.geyser.api.event.bedrock.SessionLoginEvent;
import org.geysermc.geyser.api.event.bedrock.SessionSkinApplyEvent;
import org.geysermc.geyser.api.skin.Skin;
import org.geysermc.geyser.api.skin.SkinGeometry;
import org.geysermc.geyser.session.GeyserSession;
import org.geysermc.geyser.session.auth.BedrockClientData;

/**
 * Floodgate aplica a cada jugador Bedrock la conversión Java de su skin (API de GeyserMC) y, si existe,
 * Geyser la manda también a los clientes Bedrock: la skin original y su geometría se pierden.
 *
 * <ul>
 * <li>default: se cancela esa aplicación (Java ve la skin por defecto) y Geyser recurre a la skin Bedrock
 * guardada en su caché. Geyser sólo guarda las de hasta 128×128 que no son persona; las demás se guardan
 * aquí con su propio método interno.</li>
 * <li>converted: Java ve la conversión y, mediante el evento oficial SessionSkinApplyEvent, los clientes
 * Bedrock reciben la skin original.</li>
 * </ul>
 */
public final class MclanSkins extends JavaPlugin {
    private static final int GEYSER_LIMIT = 128 * 128 * 4;
    private static final int MAX_SKIN = 512 * 512 * 4;
    private Method storeSkin;
    private Method storeGeometry;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        String mode = getConfig().getString("java-skin", "default");
        if (!"default".equals(mode) && !"converted".equals(mode)) {
            getLogger().warning("Unknown java-skin '" + mode + "'; using default.");
            mode = "default";
        }
        EventRegistrar owner = EventRegistrar.of(this);
        if ("converted".equals(mode)) {
            GeyserApi.api().eventBus().subscribe(owner, SessionSkinApplyEvent.class, this::originalForBedrock);
            getLogger().info("java-skin: converted. Java sees the converted skin; Bedrock sees the original.");
            return;
        }
        FloodgateApi.getInstance().getEventBus().subscribe(SkinApplyEvent.class, event -> event.setCancelled(true));
        try {
            Class<?> provider = Class.forName("org.geysermc.geyser.skin.SkinProvider");
            storeSkin = provider.getDeclaredMethod("storeBedrockSkin", UUID.class, String.class, byte[].class);
            storeGeometry = provider.getDeclaredMethod("storeBedrockGeometry", UUID.class, byte[].class, byte[].class);
            storeSkin.setAccessible(true);
            storeGeometry.setAccessible(true);
            GeyserApi.api().eventBus().subscribe(owner, SessionLoginEvent.class, event -> keepLargeSkin(event.connection()));
        } catch (ReflectiveOperationException | RuntimeException e) {
            getLogger().warning("This Geyser build has no usable skin cache (" + e + "); Bedrock skins larger than "
                    + "128x128 will show the default skin.");
        }
        getLogger().info("java-skin: default. Java sees the default skin; Bedrock sees the original Bedrock skin.");
    }

    /** Antes de unirse al servidor Java: Geyser aún no ha construido la lista de jugadores. */
    private void keepLargeSkin(GeyserConnection connection) {
        if (!(connection instanceof GeyserSession session)) {
            return;
        }
        BedrockClientData data = session.getClientData();
        byte[] skin = data.getSkinData();
        // Geyser ya guarda las que caben en su límite; las enormes se descartan.
        if (skin == null || skin.length > MAX_SKIN || (skin.length <= GEYSER_LIMIT && !data.isPersonaSkin())) {
            return;
        }
        try {
            // UUID de Floodgate de una cuenta no vinculada: 0 y el XUID. Las vinculadas usan su skin Java.
            UUID uuid = new UUID(0, Long.parseLong(connection.xuid()));
            storeSkin.invoke(null, uuid, data.getSkinId(), skin);
            storeGeometry.invoke(null, uuid, data.getGeometryName(), data.getGeometryData());
            getLogger().info("Kept the original " + data.getSkinImageWidth() + "x" + data.getSkinImageHeight()
                    + " Bedrock skin of " + connection.bedrockUsername() + ".");
        } catch (ReflectiveOperationException | RuntimeException e) {
            getLogger().warning("Could not keep the Bedrock skin of " + connection.bedrockUsername() + ": " + e);
        }
    }

    private void originalForBedrock(SessionSkinApplyEvent event) {
        if (!event.bedrock() || !(GeyserApi.api().connectionByUuid(event.uuid()) instanceof GeyserSession owner)) {
            return;
        }
        BedrockClientData data = owner.getClientData();
        if (data.getSkinData() == null || data.getSkinData().length > MAX_SKIN) {
            return;
        }
        event.skin(new Skin(data.getSkinId(), data.getSkinData()));
        event.geometry(new SkinGeometry(new String(data.getGeometryName(), StandardCharsets.UTF_8),
                new String(data.getGeometryData(), StandardCharsets.UTF_8)));
    }
}
