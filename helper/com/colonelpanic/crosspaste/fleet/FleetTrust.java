package com.colonelpanic.crosspaste.fleet;

import android.app.Application;
import android.content.Context;
import android.database.Cursor;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Seeds CrossPaste trust rows (peer public keys and sync runtime entries) from the published fleet
 * list. Never throws into the host application.
 */
public final class FleetTrust implements Runnable {
    private static final String TAG = "CrossPasteFleet";
    private static final String FLEET_URL =
            "https://raw.githubusercontent.com/colonelpanic8/dotfiles/master/nix-shared/crosspaste-fleet.json";
    private static final String FLEET_FILE = "crosspaste-fleet.json";
    private static final String DB_NAME = "crosspaste.db";
    private static final long APPLY_INTERVAL_MS = 60_000L;
    private static final long FETCH_INTERVAL_MS = 15 * 60_000L;
    private static final int TIMEOUT_MS = 10_000;
    private static final int MAX_BYTES = 256 * 1024;
    private static final int MAX_DEPTH = 5;
    private static final int CONNECT_STATE_DISCONNECTED = 2;
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_-]{1,128}");
    private static final Pattern HOST = Pattern.compile("[A-Za-z0-9.:%-]{1,253}");
    private static final String SQLITE = "io.requery.android.database.sqlite.SQLiteDatabase";
    // SubjectPublicKeyInfo header for an uncompressed ECDH/ECDSA P-256 key.
    private static final byte[] P256_SPKI_PREFIX = {
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, (byte) 0x86, 0x48, (byte) 0xce, 0x3d, 0x02, 0x01,
        0x06, 0x08, 0x2a, (byte) 0x86, 0x48, (byte) 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
        0x04
    };

    private static boolean started;

    private final Context context;
    private final String ownId;
    private File database;
    private String lastProblem;

    private static final class Member {
        String name;
        String id;
        String platformName;
        String platformArch;
        long platformBitMode;
        String platformVersion;
        byte[] key;
        List<String> hosts;
        long port;
    }

    public static void start(Context context) {
        try {
            synchronized (FleetTrust.class) {
                if (started) {
                    return;
                }
                started = true;
            }
            String process = Application.getProcessName();
            if (!context.getPackageName().equals(process)) {
                Log.i(TAG, "Skipping fleet trust in process " + process);
                return;
            }
            FleetTrust trust = new FleetTrust(context);
            trust.startup();
            Thread thread = new Thread(trust, TAG);
            thread.setDaemon(true);
            thread.setPriority(Thread.MIN_PRIORITY);
            thread.start();
        } catch (Throwable error) {
            Log.e(TAG, "Fleet trust disabled", error);
        }
    }

    private FleetTrust(Context context) {
        this.context = context;
        this.ownId =
                Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
    }

    private void startup() {
        List<Member> fleet = loadLocal();
        if (fleet == null) {
            return;
        }
        Member own = findOwn(fleet);
        Log.i(
                TAG,
                "Own appInstanceId from ANDROID_ID="
                        + ownId
                        + (own == null
                                ? " matches no fleet member"
                                : " matches fleet member " + own.name));
        apply(fleet, true);
    }

    @Override
    public void run() {
        long nextFetch = 0;
        while (true) {
            try {
                long now = SystemClock.elapsedRealtime();
                boolean updated = false;
                if (now >= nextFetch) {
                    nextFetch = now + FETCH_INTERVAL_MS;
                    updated = fetch();
                }
                List<Member> fleet = loadLocal();
                if (fleet != null) {
                    apply(fleet, updated);
                }
            } catch (Throwable error) {
                problem("Fleet trust cycle failed", error);
            }
            try {
                Thread.sleep(APPLY_INTERVAL_MS);
            } catch (InterruptedException error) {
                return;
            }
        }
    }

    private List<Member> loadLocal() {
        File cache = new File(context.getFilesDir(), FLEET_FILE);
        if (cache.isFile()) {
            try {
                return parse(readAll(new FileInputStream(cache)));
            } catch (Exception error) {
                problem("Ignoring invalid cached fleet " + cache, error);
            }
        }
        try {
            return parse(readAll(context.getAssets().open(FLEET_FILE)));
        } catch (Exception error) {
            problem("No usable fleet list", error);
            return null;
        }
    }

    private boolean fetch() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(FLEET_URL).openConnection();
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            connection.setUseCaches(false);
            connection.setInstanceFollowRedirects(false);
            int status = connection.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                problem("Fleet fetch returned HTTP " + status, null);
                return false;
            }
            byte[] body = readAll(connection.getInputStream());
            List<Member> fleet = parse(body);
            File cache = new File(context.getFilesDir(), FLEET_FILE);
            byte[] current = cache.isFile() ? readAll(new FileInputStream(cache)) : null;
            boolean changed = !Arrays.equals(body, current);
            if (changed) {
                writeAtomically(cache, body);
            }
            Log.i(
                    TAG,
                    "Fetched fleet: "
                            + fleet.size()
                            + " members, cache "
                            + (changed ? "updated" : "unchanged"));
            return changed;
        } catch (Exception error) {
            problem("Fleet fetch failed", error);
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private Member findOwn(List<Member> fleet) {
        for (Member member : fleet) {
            if (member.id.equals(ownId)) {
                return member;
            }
        }
        return null;
    }

    private void apply(List<Member> fleet, boolean verbose) {
        if (ownId == null || findOwn(fleet) == null) {
            problem("ANDROID_ID " + ownId + " is not a fleet member; not applying", null);
            return;
        }
        File file = locateDatabase();
        if (file == null) {
            return;
        }
        Db db = null;
        try {
            db = Db.open(context, file);
            if (!db.hasTable("CryptPublicKey") || !db.hasTable("SyncRuntimeInfoEntity")) {
                problem("Trust tables are not created yet in " + file, null);
                return;
            }
            int keys = 0;
            int rows = 0;
            int hosts = 0;
            long now = System.currentTimeMillis();
            db.begin();
            try {
                for (Member member : fleet) {
                    if (member.id.equals(ownId)) {
                        continue;
                    }
                    byte[] key =
                            db.blob(
                                    "SELECT serialized FROM CryptPublicKey WHERE appInstanceId = ?",
                                    member.id);
                    if (!Arrays.equals(key, member.key)) {
                        db.exec(
                                "INSERT OR REPLACE INTO CryptPublicKey(appInstanceId, serialized)"
                                        + " VALUES (?, ?)",
                                member.id,
                                member.key);
                        keys++;
                    }
                    String[] hostInfo =
                            db.strings(
                                    "SELECT hostInfo FROM SyncRuntimeInfoEntity"
                                            + " WHERE appInstanceId = ?",
                                    member.id);
                    if (hostInfo == null) {
                        db.exec(
                                "INSERT INTO SyncRuntimeInfoEntity(appInstanceId, appVersion,"
                                        + " userName, deviceId, deviceName, platformName,"
                                        + " platformArch, platformBitMode, platformVersion,"
                                        + " hostInfo, port, connectState, createTime, modifyTime)"
                                        + " VALUES (?, '0', 'fleet', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                                        + " ?)",
                                member.id,
                                member.id,
                                member.name,
                                member.platformName,
                                member.platformArch,
                                member.platformBitMode,
                                member.platformVersion,
                                mergeHosts("[]", member.hosts).toString(),
                                member.port,
                                (long) CONNECT_STATE_DISCONNECTED,
                                now,
                                now);
                        rows++;
                        continue;
                    }
                    JSONArray merged;
                    try {
                        merged = mergeHosts(hostInfo[0], member.hosts);
                    } catch (JSONException error) {
                        problem("Leaving unparseable hostInfo for " + member.name, error);
                        continue;
                    }
                    int added = merged.length() - new JSONArray(hostInfo[0]).length();
                    if (added > 0) {
                        db.exec(
                                "UPDATE SyncRuntimeInfoEntity SET hostInfo = ?, modifyTime = ?"
                                        + " WHERE appInstanceId = ?",
                                merged.toString(),
                                now,
                                member.id);
                        hosts += added;
                    }
                }
                db.success();
            } finally {
                db.end();
            }
            if (verbose || keys + rows + hosts > 0) {
                Log.i(
                        TAG,
                        "Applied fleet ("
                                + fleet.size()
                                + " members) to "
                                + file
                                + ": "
                                + keys
                                + " keys written, "
                                + rows
                                + " rows inserted, "
                                + hosts
                                + " host addresses added");
            }
            if (verbose) {
                for (Member member : fleet) {
                    if (!member.id.equals(ownId)) {
                        logPeer(db, member);
                    }
                }
            }
            lastProblem = null;
        } catch (Throwable error) {
            problem("Applying fleet to " + file + " failed", error);
        } finally {
            if (db != null) {
                db.close();
            }
        }
    }

    private void logPeer(Db db, Member member) throws Exception {
        byte[] key =
                db.blob("SELECT serialized FROM CryptPublicKey WHERE appInstanceId = ?", member.id);
        String[] row =
                db.strings(
                        "SELECT deviceName, hostInfo, connectState FROM SyncRuntimeInfoEntity"
                                + " WHERE appInstanceId = ?",
                        member.id);
        Log.i(
                TAG,
                "Peer "
                        + member.name
                        + " ("
                        + member.id
                        + "): key "
                        + (key == null
                                ? "missing"
                                : Arrays.equals(key, member.key) ? "current" : "different")
                        + ", row "
                        + (row == null
                                ? "missing"
                                : "present deviceName="
                                        + row[0]
                                        + " hostInfo="
                                        + row[1]
                                        + " connectState="
                                        + row[2]));
    }

    private File locateDatabase() {
        if (database != null && database.isFile()) {
            return database;
        }
        List<File> found = new ArrayList<>();
        try {
            search(context.getDataDir().getCanonicalFile(), 0, found);
        } catch (IOException error) {
            problem("Cannot resolve app data directory", error);
            return null;
        }
        if (found.size() != 1) {
            problem("Expected one " + DB_NAME + " under app data, found " + found, null);
            return null;
        }
        database = found.get(0);
        Log.i(TAG, "Using database " + database);
        return database;
    }

    private static void search(File directory, int depth, List<File> found) {
        File[] children = directory.listFiles();
        if (children == null) {
            return;
        }
        for (File child : children) {
            try {
                if (!child.getCanonicalPath().equals(child.getAbsolutePath())) {
                    continue;
                }
            } catch (IOException error) {
                continue;
            }
            if (child.isFile() && child.getName().equals(DB_NAME)) {
                found.add(child);
            } else if (child.isDirectory() && depth < MAX_DEPTH) {
                search(child, depth + 1, found);
            }
        }
    }

    private static JSONArray mergeHosts(String json, List<String> hosts) throws JSONException {
        JSONArray array = new JSONArray(json);
        Set<String> known = new HashSet<>();
        for (int index = 0; index < array.length(); index++) {
            known.add(array.getJSONObject(index).optString("hostAddress"));
        }
        for (String host : hosts) {
            if (known.add(host)) {
                JSONObject entry = new JSONObject();
                entry.put("networkPrefixLength", 32);
                entry.put("hostAddress", host);
                entry.put("lastSeen", 0);
                array.put(entry);
            }
        }
        return array;
    }

    static List<Member> parse(byte[] body) throws Exception {
        JSONObject root = new JSONObject(new String(body, "UTF-8"));
        if (!Integer.valueOf(1).equals(root.get("version"))) {
            throw new IllegalArgumentException("Unsupported fleet version " + root.get("version"));
        }
        JSONArray members = root.getJSONArray("members");
        List<Member> fleet = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < members.length(); index++) {
            JSONObject object = members.getJSONObject(index);
            Member member = new Member();
            member.name = text(object, "name");
            member.id = text(object, "appInstanceId");
            if (!ID.matcher(member.id).matches() || !ids.add(member.id)) {
                throw new IllegalArgumentException("Invalid or duplicate id " + member.id);
            }
            JSONObject platform = object.getJSONObject("platform");
            member.platformName = text(platform, "name");
            member.platformArch = text(platform, "arch");
            member.platformBitMode = integer(platform, "bitMode", 1, 128);
            member.platformVersion = text(platform, "version");
            member.key = publicKey(text(object, "cryptPublicKey"));
            JSONArray hosts = object.getJSONArray("hostAddresses");
            member.hosts = new ArrayList<>();
            for (int hostIndex = 0; hostIndex < hosts.length(); hostIndex++) {
                Object host = hosts.get(hostIndex);
                if (!(host instanceof String) || !HOST.matcher((String) host).matches()) {
                    throw new IllegalArgumentException("Invalid host for " + member.name);
                }
                member.hosts.add((String) host);
            }
            if (member.hosts.isEmpty()) {
                throw new IllegalArgumentException("No host addresses for " + member.name);
            }
            member.port = integer(object, "port", 1, 65535);
            fleet.add(member);
        }
        return fleet;
    }

    private static String text(JSONObject object, String name) throws JSONException {
        Object value = object.get(name);
        if (!(value instanceof String) || ((String) value).isEmpty()) {
            throw new JSONException("Expected non-empty string " + name);
        }
        return (String) value;
    }

    private static long integer(JSONObject object, String name, long min, long max)
            throws JSONException {
        Object value = object.get(name);
        if (!(value instanceof Integer || value instanceof Long)) {
            throw new JSONException("Expected integer " + name);
        }
        long number = ((Number) value).longValue();
        if (number < min || number > max) {
            throw new JSONException(name + " out of range");
        }
        return number;
    }

    private static byte[] publicKey(String encoded) throws Exception {
        byte[] der = Base64.getDecoder().decode(encoded);
        byte[] prefix = Arrays.copyOf(der, P256_SPKI_PREFIX.length);
        if (der.length != 91 || !Arrays.equals(prefix, P256_SPKI_PREFIX)) {
            throw new IllegalArgumentException("cryptPublicKey is not a P-256 SPKI key");
        }
        PublicKey key = KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(der));
        if (!(key instanceof ECPublicKey)
                || ((ECPublicKey) key).getParams().getCurve().getField().getFieldSize() != 256) {
            throw new IllegalArgumentException("cryptPublicKey is not on P-256");
        }
        return der;
    }

    private static byte[] readAll(InputStream input) throws IOException {
        try (InputStream stream = input) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int count;
            while ((count = stream.read(buffer)) != -1) {
                output.write(buffer, 0, count);
                if (output.size() > MAX_BYTES) {
                    throw new IOException("Fleet list exceeds " + MAX_BYTES + " bytes");
                }
            }
            return output.toByteArray();
        }
    }

    private static void writeAtomically(File target, byte[] body) throws IOException {
        File temporary = new File(target.getParentFile(), target.getName() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(body);
            output.getFD().sync();
        }
        if (!temporary.renameTo(target)) {
            temporary.delete();
            throw new IOException("Could not replace " + target);
        }
    }

    private void problem(String message, Throwable error) {
        String summary = error == null ? message : message + ": " + rootCause(error);
        if (!summary.equals(lastProblem)) {
            Log.w(TAG, summary);
            lastProblem = summary;
        }
    }

    private static String rootCause(Throwable error) {
        while (error instanceof InvocationTargetException && error.getCause() != null) {
            error = error.getCause();
        }
        return error.toString();
    }

    /** The app's bundled requery SQLite, so locking stays within one SQLite library. */
    private static final class Db {
        private final Object handle;

        private Db(Object handle) {
            this.handle = handle;
        }

        static Db open(Context context, File file) throws Exception {
            ClassLoader loader = context.getClassLoader();
            Class<?> type = Class.forName(SQLITE, true, loader);
            Class<?> factory = Class.forName(SQLITE + "$CursorFactory", true, loader);
            int flags = type.getField("OPEN_READWRITE").getInt(null);
            // Match the file's journal mode; requery rewrites journal_mode on open.
            if (isWal(file)) {
                flags |= type.getField("ENABLE_WRITE_AHEAD_LOGGING").getInt(null);
            }
            Method open = type.getMethod("openDatabase", String.class, factory, int.class);
            return new Db(open.invoke(null, file.getPath(), null, flags));
        }

        private static boolean isWal(File file) throws IOException {
            byte[] header = new byte[20];
            try (FileInputStream input = new FileInputStream(file)) {
                int offset = 0;
                while (offset < header.length) {
                    int count = input.read(header, offset, header.length - offset);
                    if (count < 0) {
                        throw new IOException("Truncated SQLite header");
                    }
                    offset += count;
                }
            }
            if (!new String(header, 0, 16, "US-ASCII").equals("SQLite format 3\0")) {
                throw new IOException("Not an SQLite database");
            }
            return header[18] == 2 && header[19] == 2;
        }

        private Object call(String name, Class<?>[] types, Object... args) throws Exception {
            return handle.getClass().getMethod(name, types).invoke(handle, args);
        }

        boolean hasTable(String name) throws Exception {
            return strings("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", name)
                    != null;
        }

        private Cursor query(String sql, Object... args) throws Exception {
            return (Cursor)
                    call("rawQuery", new Class<?>[] {String.class, Object[].class}, sql, args);
        }

        byte[] blob(String sql, Object... args) throws Exception {
            try (Cursor cursor = query(sql, args)) {
                return cursor.moveToFirst() ? cursor.getBlob(0) : null;
            }
        }

        String[] strings(String sql, Object... args) throws Exception {
            try (Cursor cursor = query(sql, args)) {
                if (!cursor.moveToFirst()) {
                    return null;
                }
                String[] row = new String[cursor.getColumnCount()];
                for (int index = 0; index < row.length; index++) {
                    row[index] = cursor.getString(index);
                }
                return row;
            }
        }

        void exec(String sql, Object... args) throws Exception {
            call("execSQL", new Class<?>[] {String.class, Object[].class}, sql, args);
        }

        void begin() throws Exception {
            call("beginTransaction", new Class<?>[0]);
        }

        void success() throws Exception {
            call("setTransactionSuccessful", new Class<?>[0]);
        }

        void end() throws Exception {
            call("endTransaction", new Class<?>[0]);
        }

        void close() {
            try {
                call("close", new Class<?>[0]);
            } catch (Throwable error) {
                Log.w(TAG, "Closing database failed: " + rootCause(error));
            }
        }
    }
}
