package dev.mclan.packs;

import java.nio.file.Path;
import java.util.Objects;
import java.util.UUID;
import org.geysermc.geyser.api.GeyserApi;
import org.geysermc.geyser.api.event.EventRegistrar;
import org.geysermc.geyser.api.event.bedrock.SessionLoadResourcePacksEvent;
import org.geysermc.geyser.api.pack.PackCodec;
import org.geysermc.geyser.api.pack.ResourcePack;
import org.geysermc.geyser.api.pack.option.ResourcePackOption;

/** Variante Bedrock por la API de Geyser: se registra en cada sesión al conectar. Sólo si Geyser está activo. */
final class BedrockPacks {
    private volatile ResourcePack pack;
    private Path file;

    BedrockPacks(MclanPacks plugin) {
        GeyserApi.api().eventBus().subscribe(EventRegistrar.of(plugin), SessionLoadResourcePacksEvent.class, event -> {
            ResourcePack current = pack;
            if (current != null) {
                try {
                    event.register(current, new ResourcePackOption<?>[0]);
                } catch (RuntimeException e) {
                    plugin.getLogger().warning("Geyser refused the Bedrock pack for " + event.connection().bedrockUsername() + ": " + e);
                }
            }
        });
    }

    /** Cambia el paquete de las próximas conexiones; devuelve el error si Geyser no lo acepta. */
    String use(Path next) {
        if (Objects.equals(next, file) && (next == null || pack != null)) {
            return null;
        }
        file = next;
        pack = null;
        if (next == null) {
            return null;
        }
        try {
            // Geyser lee el manifiesto y calcula el hash una vez; la misma instancia sirve a todas las sesiones.
            pack = ResourcePack.create(PackCodec.path(next));
            return null;
        } catch (RuntimeException e) {
            return String.valueOf(e.getMessage());
        }
    }

    boolean isBedrock(UUID uuid) {
        return GeyserApi.api().connectionByUuid(uuid) != null;
    }
}
