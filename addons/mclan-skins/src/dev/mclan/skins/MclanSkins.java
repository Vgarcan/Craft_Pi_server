package dev.mclan.skins;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.bukkit.plugin.java.JavaPlugin;
import org.cloudburstmc.protocol.bedrock.data.skin.AnimatedTextureType;
import org.cloudburstmc.protocol.bedrock.data.skin.AnimationData;
import org.cloudburstmc.protocol.bedrock.data.skin.AnimationExpressionType;
import org.cloudburstmc.protocol.bedrock.data.skin.ImageData;
import org.cloudburstmc.protocol.bedrock.data.skin.PersonaPieceData;
import org.cloudburstmc.protocol.bedrock.data.skin.PersonaPieceTintData;
import org.cloudburstmc.protocol.bedrock.data.skin.SerializedSkin;
import org.geysermc.floodgate.api.FloodgateApi;
import org.geysermc.floodgate.api.event.skin.SkinApplyEvent;
import org.geysermc.geyser.api.GeyserApi;
import org.geysermc.geyser.api.connection.GeyserConnection;
import org.geysermc.geyser.api.event.EventRegistrar;
import org.geysermc.geyser.api.event.bedrock.SessionDisconnectEvent;
import org.geysermc.geyser.api.event.bedrock.SessionLoginEvent;
import org.geysermc.geyser.session.GeyserSession;
import org.geysermc.geyser.session.UpstreamSession;
import org.geysermc.geyser.session.auth.BedrockClientData;
import org.jose4j.json.JsonUtil;

/**
 * Skins de jugadores Bedrock en un servidor Java con Geyser y Floodgate.
 *
 * <p>Geyser reconstruye cada skin con sólo imagen, geometría y brazos: descarta piezas y tintes persona
 * (creador de personajes) y animaciones, y el cliente rechaza el resultado y muestra una skin por defecto.
 * Este plugin guarda al iniciar sesión la skin completa que firmó el cliente y la pone en cada entrada de
 * la lista de jugadores que Geyser envía a los clientes Bedrock, como hace el servidor oficial de Bedrock.
 *
 * <p>java-skin: default cancela además la conversión Java de Floodgate (API de GeyserMC): Java ve la skin
 * por defecto. Con converted, Java ve esa conversión. Bedrock ve la original en los dos casos.
 */
public final class MclanSkins extends JavaPlugin {
    private static final int MAX_IMAGE = 512 * 512 * 4;
    /** XUID → skin completa. Geyser pone el XUID en todas las entradas, también en la del propio jugador. */
    private final Map<String, SerializedSkin> skins = new ConcurrentHashMap<>();
    private Field upstreamField;
    private Field queueField;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        String mode = getConfig().getString("java-skin", "default");
        if (!"default".equals(mode) && !"converted".equals(mode)) {
            getLogger().warning("Unknown java-skin '" + mode + "'; using default.");
            mode = "default";
        }
        if ("default".equals(mode)) {
            FloodgateApi.getInstance().getEventBus().subscribe(SkinApplyEvent.class, event -> event.setCancelled(true));
        }
        try {
            upstreamField = GeyserSession.class.getDeclaredField("upstream");
            queueField = UpstreamSession.class.getDeclaredField("postStartGamePackets");
            upstreamField.setAccessible(true);
            queueField.setAccessible(true);
        } catch (ReflectiveOperationException | RuntimeException e) {
            getLogger().warning("This Geyser build cannot be hooked (" + e + "); Bedrock players get Geyser's own skins.");
            return;
        }
        EventRegistrar owner = EventRegistrar.of(this);
        GeyserApi.api().eventBus().subscribe(owner, SessionLoginEvent.class, event -> login(event.connection()));
        GeyserApi.api().eventBus().subscribe(owner, SessionDisconnectEvent.class,
                event -> skins.remove(event.connection().xuid()));
        getLogger().info("java-skin: " + mode + ". Bedrock players see complete original Bedrock skins.");
    }

    /** Antes de unirse al servidor Java: aún no se ha enviado ninguna lista de jugadores. */
    private void login(GeyserConnection connection) {
        if (!(connection instanceof GeyserSession session)) {
            return;
        }
        try {
            SerializedSkin skin = completeSkin(session.getClientData());
            if (skin != null) {
                skins.put(connection.xuid(), skin);
                getLogger().info("Complete skin of " + connection.bedrockUsername() + ": " + skin.getSkinData().getWidth()
                        + "x" + skin.getSkinData().getHeight() + ", persona=" + skin.isPersona() + ", pieces="
                        + skin.getPersonaPieces().size() + ", animations=" + skin.getAnimations().size() + ".");
            }
            UpstreamSession current = session.getUpstream();
            if (!(current instanceof SkinUpstream)) {
                SkinUpstream replacement = new SkinUpstream(current.getSession(), skins);
                replacement.setInitialized(current.isInitialized());
                queueField.set(replacement, queueField.get(current));
                upstreamField.set(session, replacement);
            }
        } catch (Exception e) {
            getLogger().warning("Could not handle the skin of " + connection.bedrockUsername() + ": " + e);
        }
    }

    /** La skin tal como la firmó el cliente (datos del login). */
    static SerializedSkin completeSkin(BedrockClientData data) throws Exception {
        String jwt = data.getOriginalString();
        String[] parts = jwt == null ? new String[0] : jwt.split("\\.");
        if (parts.length != 3 || data.getSkinData() == null || data.getSkinData().length > MAX_IMAGE) {
            return null;
        }
        Map<String, Object> claims = JsonUtil.parseJson(new String(Base64.getUrlDecoder().decode(parts[1]),
                StandardCharsets.UTF_8));
        List<AnimationData> animations = new ArrayList<>();
        for (Object item : list(claims, "AnimatedImageData")) {
            Map<?, ?> animation = (Map<?, ?>) item;
            byte[] image = Base64.getDecoder().decode((String) animation.get("Image"));
            if (image.length > MAX_IMAGE) {
                return null;
            }
            animations.add(new AnimationData(
                    ImageData.of(number(animation, "ImageWidth"), number(animation, "ImageHeight"), image),
                    AnimatedTextureType.values()[number(animation, "Type")],
                    ((Number) animation.get("Frames")).floatValue(),
                    AnimationExpressionType.values()[number(animation, "AnimationExpression")]));
        }
        List<PersonaPieceData> pieces = new ArrayList<>();
        for (Object item : list(claims, "PersonaPieces")) {
            Map<?, ?> piece = (Map<?, ?>) item;
            pieces.add(new PersonaPieceData(text(piece, "PieceId"), text(piece, "PieceType"), text(piece, "PackId"),
                    Boolean.TRUE.equals(piece.get("IsDefault")), text(piece, "ProductId")));
        }
        List<PersonaPieceTintData> tints = new ArrayList<>();
        for (Object item : list(claims, "PieceTintColors")) {
            Map<?, ?> tint = (Map<?, ?>) item;
            List<String> colors = new ArrayList<>();
            for (Object color : (List<?>) tint.get("Colors")) {
                colors.add(String.valueOf(color));
            }
            tints.add(new PersonaPieceTintData(text(tint, "PieceType"), colors));
        }
        byte[] cape = data.getCapeData();
        String engine = text(claims, "SkinGeometryDataEngineVersion");
        return SerializedSkin.builder()
                .skinId(data.getSkinId())
                .playFabId(text(claims, "PlayFabId"))
                .skinResourcePatch(new String(data.getGeometryName(), StandardCharsets.UTF_8))
                .skinData(ImageData.of(data.getSkinImageWidth(), data.getSkinImageHeight(), data.getSkinData()))
                .animations(animations)
                .capeData(cape == null || cape.length == 0 ? ImageData.EMPTY
                        : ImageData.of(data.getCapeImageWidth(), data.getCapeImageHeight(), cape))
                .geometryData(new String(data.getGeometryData(), StandardCharsets.UTF_8))
                .geometryDataEngineVersion(engine.isEmpty() ? ""
                        : new String(Base64.getDecoder().decode(engine), StandardCharsets.UTF_8))
                .animationData(text(claims, "SkinAnimationData"))
                .capeId(data.getCapeId() == null ? "" : data.getCapeId())
                .fullSkinId(data.getSkinId() + (data.getCapeId() == null ? "" : data.getCapeId()))
                .armSize(data.getArmSize() == null ? "wide" : data.getArmSize())
                .skinColor(data.getSkinColor() == null ? "#0" : data.getSkinColor())
                .personaPieces(pieces)
                .tintColors(tints)
                .premium(data.isPremiumSkin())
                .persona(data.isPersonaSkin())
                .capeOnClassic(data.isCapeOnClassicSkin())
                .overridingPlayerAppearance(true)
                .trusted(true)
                .build();
    }

    private static List<?> list(Map<?, ?> map, String key) {
        return map.get(key) instanceof List<?> value ? value : List.of();
    }

    private static int number(Map<?, ?> map, String key) {
        return ((Number) map.get(key)).intValue();
    }

    private static String text(Map<?, ?> map, String key) {
        Object value = map.get(key);
        return value == null ? "" : String.valueOf(value);
    }
}
