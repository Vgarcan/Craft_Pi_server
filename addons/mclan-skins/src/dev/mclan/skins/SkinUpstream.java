package dev.mclan.skins;

import java.util.Map;
import org.cloudburstmc.protocol.bedrock.BedrockServerSession;
import org.cloudburstmc.protocol.bedrock.data.skin.SerializedSkin;
import org.cloudburstmc.protocol.bedrock.packet.BedrockPacket;
import org.cloudburstmc.protocol.bedrock.packet.PlayerListPacket;
import org.geysermc.geyser.session.UpstreamSession;

/** Salida hacia un cliente Bedrock: cambia la skin reconstruida por Geyser por la completa del jugador. */
final class SkinUpstream extends UpstreamSession {
    private final Map<String, SerializedSkin> skins;

    SkinUpstream(BedrockServerSession session, Map<String, SerializedSkin> skins) {
        super(session);
        this.skins = skins;
    }

    @Override
    public void sendPacket(BedrockPacket packet) {
        super.sendPacket(complete(packet));
    }

    @Override
    public void sendPacketImmediately(BedrockPacket packet) {
        super.sendPacketImmediately(complete(packet));
    }

    @Override
    public void queuePostStartGamePacket(BedrockPacket packet) {
        super.queuePostStartGamePacket(complete(packet));
    }

    private BedrockPacket complete(BedrockPacket packet) {
        // Sólo las entradas que añaden llevan skin; las de borrado no.
        if (packet instanceof PlayerListPacket list) {
            for (PlayerListPacket.Entry entry : list.getEntries()) {
                SerializedSkin skin = entry.getXuid() == null ? null : skins.get(entry.getXuid());
                if (skin != null && entry.getSkin() != null) {
                    entry.setSkin(skin);
                }
            }
        }
        return packet;
    }
}
