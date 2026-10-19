package dev.mclan.packs;

import com.viaversion.viaversion.api.Via;
import com.viaversion.viaversion.api.protocol.version.ProtocolVersion;
import java.util.Set;
import java.util.UUID;

/** Versión real de cada cliente Java según ViaVersion. Sólo se carga si ViaVersion está activo. */
final class ClientVersions {
    ClientVersions() {
        Via.getAPI().getServerVersion();
    }

    private static ProtocolVersion of(UUID uuid) {
        return Via.getAPI().getPlayerProtocolVersion(uuid);
    }

    /** Versiones que cubre el protocolo del cliente, p. ej. [1.21.2, 1.21.3]. */
    Set<String> included(UUID uuid) {
        ProtocolVersion version = of(uuid);
        return version == null || !version.isKnown() ? Set.of() : version.getIncludedVersions();
    }

    boolean newerThanServer(UUID uuid) {
        ProtocolVersion version = of(uuid);
        return version != null && version.isKnown()
                && version.newerThan(Via.getAPI().getServerVersion().highestSupportedProtocolVersion());
    }

    String name(UUID uuid) {
        ProtocolVersion version = of(uuid);
        return version == null ? "unknown" : version.getName();
    }
}
