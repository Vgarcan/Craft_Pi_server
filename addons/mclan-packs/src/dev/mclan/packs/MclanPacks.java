package dev.mclan.packs;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.io.Reader;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Pattern;
import net.kyori.adventure.audience.Audience;
import net.kyori.adventure.text.Component;
import org.bukkit.Bukkit;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerResourcePackStatusEvent;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Paquete de recursos del servidor para Java y Bedrock.
 *
 * <p>manage.sh pack escribe /opt/packs/active.properties (montado de sólo lectura). Las variantes Java
 * se sirven por HTTP en el puerto 8101, que Compose publica sólo en la IP LAN, y cada jugador Java
 * recibe la hecha para su versión: ViaVersion dice cuál usa el cliente (QuestCraft en 1.21.5, un PC
 * en 26.3). Un cliente sin variante propia no recibe ninguna, salvo que sea más nuevo que el servidor,
 * que recibe la del servidor. Los jugadores Bedrock reciben su variante por Geyser al conectar.
 *
 * <p>«mclanpacks reload» vuelve a leer la selección y la aplica en caliente a los jugadores Java.
 */
public final class MclanPacks extends JavaPlugin implements Listener {
    static final Path ROOT = Path.of("/opt/packs");
    private static final int PORT = 8101;
    private static final Pattern SHA1 = Pattern.compile("[0-9a-f]{40}");
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern VERSION = Pattern.compile("[0-9]+(?:\\.[0-9]+){1,2}");
    private static final Pattern URL = Pattern.compile("http://[0-9]{1,3}(?:\\.[0-9]{1,3}){3}:8101/");
    private static final Pattern TEXT = Pattern.compile("[ -~]{0,200}");

    record Variant(String label, Set<String> versions, String sha1, Path file, UUID id) {}

    record Selection(String name, String prompt, boolean required, List<Variant> java, Path bedrock) {
        static final Selection NONE = new Selection("", "", false, List.of(), null);
    }

    private volatile Selection selection = Selection.NONE;
    private volatile Map<String, Path> routes = Map.of();
    /** Variante enviada a cada jugador Java conectado. */
    private final Map<UUID, Variant> sent = new ConcurrentHashMap<>();
    private final Map<UUID, String> results = new ConcurrentHashMap<>();
    private HttpServer http;
    private ExecutorService workers;
    private String baseUrl;
    private ClientVersions clients;
    private BedrockPacks bedrock;
    private volatile String bedrockProblem;

    @Override
    public void onEnable() {
        String url = System.getenv("MCLAN_PACKS_URL");
        if (url != null && URL.matcher(url).matches()) {
            baseUrl = url;
        } else {
            getLogger().warning("MCLAN_PACKS_URL is missing or invalid: Java players will not be offered packs.");
        }
        if (getServer().getPluginManager().isPluginEnabled("ViaVersion")) {
            try {
                clients = new ClientVersions();
            } catch (LinkageError | RuntimeException e) {
                getLogger().warning("ViaVersion API unavailable (" + e + "); every Java client counts as the server's version.");
            }
        }
        if (getServer().getPluginManager().isPluginEnabled("Geyser-Spigot")) {
            try {
                bedrock = new BedrockPacks(this);
            } catch (LinkageError | RuntimeException e) {
                getLogger().warning("Geyser API unavailable (" + e + "); Bedrock players will not get the pack.");
            }
        }
        try {
            startHttp();
        } catch (IOException e) {
            getLogger().severe("Cannot serve resource packs on port " + PORT + ": " + e);
        }
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info(load());
    }

    @Override
    public void onDisable() {
        if (http != null) {
            http.stop(0);
        }
        if (workers != null) {
            workers.shutdownNow();
        }
    }

    // --- Selección ---------------------------------------------------------------------------

    /** Lee la selección; ante cualquier error se queda sin paquete y lo explica. */
    private String load() {
        Selection next;
        try {
            next = read(ROOT.resolve("active.properties"));
        } catch (IOException | IllegalArgumentException e) {
            bedrockProblem = apply(Selection.NONE);
            return "Error: invalid pack selection (" + e.getMessage() + "); no pack is offered.";
        }
        bedrockProblem = apply(next);
        return summary();
    }

    private String summary() {
        Selection current = selection;
        if (current.java().isEmpty() && current.bedrock() == null) {
            return "No resource pack selected.";
        }
        return "Pack: " + current.name() + "; Java " + (current.java().isEmpty() ? "off" : labels(current.java()))
                + " (" + (current.required() ? "required" : "optional") + "); Bedrock "
                + (current.bedrock() == null ? "off" : bedrock == null ? "unavailable" : "on")
                + (bedrockProblem == null ? "." : "; Bedrock error: " + bedrockProblem);
    }

    private String apply(Selection next) {
        Map<String, Path> map = new HashMap<>();
        for (Variant variant : next.java()) {
            map.put("/" + variant.sha1() + ".zip", variant.file());
        }
        routes = Map.copyOf(map);
        selection = next;
        return bedrock == null ? null : bedrock.use(next.bedrock());
    }

    static Selection read(Path path) throws IOException {
        if (!Files.exists(path, LinkOption.NOFOLLOW_LINKS)) {
            return Selection.NONE;
        }
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.size(path) > 64 * 1024) {
            throw new IllegalArgumentException("not a small regular file");
        }
        Properties values = new Properties();
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            values.load(reader);
        }
        if (!"1".equals(values.getProperty("schema"))) {
            throw new IllegalArgumentException("unknown schema");
        }
        if (values.getProperty("pack", "").isEmpty()) {
            return Selection.NONE;
        }
        String name = text(values, "name");
        String prompt = text(values, "prompt");
        int count = Integer.parseInt(values.getProperty("java.count", "0"));
        if (count < 0 || count > 32) {
            throw new IllegalArgumentException("java.count out of range");
        }
        List<Variant> java = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            String label = matching(values, "java." + i + ".label", VERSION);
            Set<String> versions = new LinkedHashSet<>();
            for (String version : values.getProperty("java." + i + ".versions", "").split(",")) {
                if (!VERSION.matcher(version).matches()) {
                    throw new IllegalArgumentException("bad version in java." + i);
                }
                versions.add(version);
            }
            String sha1 = matching(values, "java." + i + ".sha1", SHA1);
            Path file = stored(values, "java." + i + ".file", sha1 + ".zip");
            UUID id = UUID.nameUUIDFromBytes(("mclan-packs:" + sha1).getBytes(StandardCharsets.UTF_8));
            java.add(new Variant(label, Set.copyOf(versions), sha1, file, id));
        }
        Path bedrockFile = null;
        String bedrockName = values.getProperty("bedrock.file");
        if (bedrockName != null) {
            String digest = bedrockName.replaceFirst("^files/", "").replaceFirst("\\.mcpack$", "");
            if (!SHA256.matcher(digest).matches()) {
                throw new IllegalArgumentException("bad bedrock.file");
            }
            bedrockFile = stored(values, "bedrock.file", digest + ".mcpack");
        }
        return new Selection(name, prompt, "true".equals(values.getProperty("required")), List.copyOf(java), bedrockFile);
    }

    private static String text(Properties values, String key) {
        String value = values.getProperty(key, "");
        if (!TEXT.matcher(value).matches()) {
            throw new IllegalArgumentException("bad " + key);
        }
        return value;
    }

    private static String matching(Properties values, String key, Pattern pattern) {
        String value = values.getProperty(key, "");
        if (!pattern.matcher(value).matches()) {
            throw new IllegalArgumentException("bad " + key);
        }
        return value;
    }

    /** Sólo files/NOMBRE, con el nombre que fija su hash, y un archivo regular (sin enlaces). */
    private static Path stored(Properties values, String key, String expected) {
        if (!("files/" + expected).equals(values.getProperty(key))) {
            throw new IllegalArgumentException("bad " + key);
        }
        Path file = ROOT.resolve("files").resolve(expected);
        if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("missing " + expected);
        }
        return file;
    }

    private static String labels(List<Variant> variants) {
        List<String> names = new ArrayList<>();
        for (Variant variant : variants) {
            names.add(variant.label());
        }
        return String.join(", ", names);
    }

    // --- Jugadores Java ----------------------------------------------------------------------

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        offer(event.getPlayer());
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        sent.remove(event.getPlayer().getUniqueId());
        results.remove(event.getPlayer().getUniqueId());
    }

    /** Envía la variante que corresponde al cliente; devuelve true si cambió lo que tenía. */
    private boolean offer(Player player) {
        UUID uuid = player.getUniqueId();
        if (bedrock != null && bedrock.isBedrock(uuid)) {
            return false;
        }
        Selection current = selection;
        Variant choice = baseUrl == null ? null : pick(uuid, current);
        Variant previous = sent.get(uuid);
        if (previous != null && previous.equals(choice)) {
            return false;
        }
        if (previous != null) {
            player.removeResourcePack(previous.id());
            sent.remove(uuid);
            results.remove(uuid);
        }
        if (choice == null) {
            if (!current.java().isEmpty()) {
                getLogger().info(player.getName() + " (" + clientName(uuid) + "): no variant of " + current.name()
                        + " for this client version; playing without the pack.");
            }
            return previous != null;
        }
        sent.put(uuid, choice);
        player.addResourcePack(choice.id(), baseUrl + choice.sha1() + ".zip", HexFormat.of().parseHex(choice.sha1()),
                current.prompt(), current.required());
        return true;
    }

    private Variant pick(UUID uuid, Selection current) {
        if (current.java().isEmpty()) {
            return null;
        }
        String server = Bukkit.getMinecraftVersion();
        Set<String> client = clients == null ? Set.of(server) : clients.included(uuid);
        for (Variant variant : current.java()) {
            if (!Collections.disjoint(variant.versions(), client)) {
                return variant;
            }
        }
        if (clients != null && clients.newerThanServer(uuid)) {
            for (Variant variant : current.java()) {
                if (variant.versions().contains(server)) {
                    return variant;
                }
            }
        }
        return null;
    }

    private String clientName(UUID uuid) {
        return clients == null ? Bukkit.getMinecraftVersion() : clients.name(uuid);
    }

    @EventHandler
    public void onStatus(PlayerResourcePackStatusEvent event) {
        Player player = event.getPlayer();
        Variant variant = sent.get(player.getUniqueId());
        if (variant == null || !variant.id().equals(event.getID())) {
            return;
        }
        PlayerResourcePackStatusEvent.Status status = event.getStatus();
        if (status == PlayerResourcePackStatusEvent.Status.ACCEPTED || status == PlayerResourcePackStatusEvent.Status.DOWNLOADED) {
            return;
        }
        Selection current = selection;
        results.put(player.getUniqueId(), status.name());
        getLogger().info(player.getName() + " (" + clientName(player.getUniqueId()) + "): " + current.name() + " "
                + variant.label() + " " + status.name());
        if (status == PlayerResourcePackStatusEvent.Status.DECLINED && current.required()) {
            player.kick(Component.text("This server requires its resource pack (" + current.name()
                    + "). Accept it when you join, or ask the operator to make it optional."));
        }
    }

    // --- HTTP --------------------------------------------------------------------------------

    private void startHttp() throws IOException {
        // Límites del servidor HTTP del JDK: se leen una sola vez, al crear el primero.
        System.setProperty("sun.net.httpserver.maxReqTime", "30");
        System.setProperty("sun.net.httpserver.maxRspTime", "900");
        System.setProperty("jdk.httpserver.maxConnections", "32");
        workers = Executors.newFixedThreadPool(4, task -> {
            Thread thread = new Thread(task, "MclanPacks-HTTP");
            thread.setDaemon(true);
            return thread;
        });
        http = HttpServer.create(new InetSocketAddress(PORT), 16);
        http.setExecutor(workers);
        http.createContext("/", this::serve);
        http.start();
    }

    private void serve(HttpExchange exchange) throws IOException {
        try {
            String method = exchange.getRequestMethod();
            if (!"GET".equals(method) && !"HEAD".equals(method)) {
                exchange.getResponseHeaders().set("Allow", "GET, HEAD");
                exchange.sendResponseHeaders(405, -1);
                return;
            }
            Path file = exchange.getRequestURI().getRawQuery() == null ? routes.get(exchange.getRequestURI().getRawPath()) : null;
            if (file == null) {
                exchange.sendResponseHeaders(404, -1);
                return;
            }
            long size = Files.size(file);
            exchange.getResponseHeaders().set("Content-Type", "application/zip");
            exchange.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
            // El nombre es su SHA-1: el contenido de una URL nunca cambia.
            exchange.getResponseHeaders().set("Cache-Control", "public, max-age=31536000, immutable");
            if ("HEAD".equals(method)) {
                exchange.sendResponseHeaders(200, -1);
                return;
            }
            exchange.sendResponseHeaders(200, size);
            try (OutputStream out = exchange.getResponseBody()) {
                Files.copy(file, out);
            }
        } finally {
            exchange.close();
        }
    }

    // --- Comando -----------------------------------------------------------------------------

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 1 && "reload".equalsIgnoreCase(args[0])) {
            String summary = load();
            int updated = 0;
            for (Player player : getServer().getOnlinePlayers()) {
                if (offer(player)) {
                    updated++;
                }
            }
            getLogger().info("Reloaded. " + summary);
            reply(sender, "Reloaded. " + summary + " Java players updated: " + updated
                    + ". Bedrock players get changes when they reconnect.");
            return true;
        }
        if (args.length == 1 && "status".equalsIgnoreCase(args[0])) {
            Selection current = selection;
            List<String> lines = new ArrayList<>();
            lines.add(summary().replace("No resource pack selected.", "Pack: none."));
            lines.add("HTTP: " + (http == null ? "not running" : baseUrl == null ? "no URL configured" : baseUrl)
                    + "; files: " + routes.size());
            for (Player player : getServer().getOnlinePlayers()) {
                UUID uuid = player.getUniqueId();
                if (bedrock != null && bedrock.isBedrock(uuid)) {
                    lines.add(player.getName() + ": Bedrock, " + (current.bedrock() == null ? "no pack" : "pack sent at login"));
                    continue;
                }
                Variant variant = sent.get(uuid);
                lines.add(player.getName() + ": Java " + clientName(uuid) + ", "
                        + (variant == null ? "no pack" : variant.label() + " " + results.getOrDefault(uuid, "PENDING")));
            }
            reply(sender, String.join("\n", lines));
            return true;
        }
        return false;
    }

    /** Por Adventure: la API de chat de BungeeCord no forma parte de las librerías de la release. */
    private static void reply(CommandSender sender, String text) {
        Audience audience = sender;
        audience.sendMessage(Component.text(text));
    }
}
