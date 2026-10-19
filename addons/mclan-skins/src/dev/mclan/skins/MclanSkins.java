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
    // Límites de lo que se reenvía a otros clientes: una skin fuera de ellos usa la de Geyser.
    private static final int MAX_ANIMATIONS = 8;
    private static final int MAX_PIECES = 64;
    private static final int MAX_TINTS = 16;
    private static final int MAX_COLORS = 8;
    private static final int MAX_ID = 256;
    private static final int MAX_GEOMETRY = 1024 * 1024;
    private static final int MAX_ANIMATION_DATA = 64 * 1024;
    private static final int MAX_PATCH = 16 * 1024;
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
        if (parts.length != 3 || !image(data.getSkinImageWidth(), data.getSkinImageHeight(), data.getSkinData())
                || data.getGeometryData() == null || data.getGeometryData().length > MAX_GEOMETRY
                || data.getGeometryName() == null || data.getGeometryName().length > MAX_PATCH) {
            return null;
        }
        Map<String, Object> claims = JsonUtil.parseJson(new String(Base64.getUrlDecoder().decode(parts[1]),
                StandardCharsets.UTF_8));
        if (list(claims, "AnimatedImageData").size() > MAX_ANIMATIONS || list(claims, "PersonaPieces").size() > MAX_PIECES
                || list(claims, "PieceTintColors").size() > MAX_TINTS
                || text(claims, "SkinAnimationData").length() > MAX_ANIMATION_DATA) {
            return null;
        }
        List<AnimationData> animations = new ArrayList<>();
        for (Object item : list(claims, "AnimatedImageData")) {
            Map<?, ?> animation = (Map<?, ?>) item;
            byte[] image = Base64.getDecoder().decode((String) animation.get("Image"));
            if (!image(number(animation, "ImageWidth"), number(animation, "ImageHeight"), image)) {
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
            if (!ids(piece, "PieceId", "PieceType", "PackId", "ProductId")) {
                return null;
            }
            pieces.add(new PersonaPieceData(text(piece, "PieceId"), text(piece, "PieceType"), text(piece, "PackId"),
                    Boolean.TRUE.equals(piece.get("IsDefault")), text(piece, "ProductId")));
        }
        List<PersonaPieceTintData> tints = new ArrayList<>();
        for (Object item : list(claims, "PieceTintColors")) {
            Map<?, ?> tint = (Map<?, ?>) item;
            List<?> given = (List<?>) tint.get("Colors");
            if (!ids(tint, "PieceType") || given.size() > MAX_COLORS) {
                return null;
            }
            List<String> colors = new ArrayList<>();
            for (Object color : given) {
                if (String.valueOf(color).length() > MAX_ID) {
                    return null;
                }
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
                .capeData(cape == null || !image(data.getCapeImageWidth(), data.getCapeImageHeight(), cape) ? ImageData.EMPTY
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

    /** Imagen RGBA cuyo tamaño declarado coincide con sus bytes y no supera el máximo. */
    private static boolean image(int width, int height, byte[] bytes) {
        return bytes != null && width > 0 && height > 0 && width <= 512 && height <= 512
                && (long) width * height * 4 == bytes.length;
    }

    private static boolean ids(Map<?, ?> map, String... keys) {
        for (String key : keys) {
            if (text(map, key).length() > MAX_ID) {
                return false;
            }
        }
        return true;
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
