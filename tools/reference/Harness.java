/*
 * Reference harness for the original Cyber Detectives Java code
 * (arc-l/cyber-detective, commit 55f57f8). It drives the ORIGINAL classes
 * (projects.cyberDetective.*, common.util.IDGenerator and the GUI class
 * projects.cyberDetective.ui.Environment) headlessly and records their
 * observable behaviour: return values, everything printed on stdout, and
 * exceptions. The golden fixtures in tests/fixtures/golden/ are produced
 * from its output (see regen_golden.sh).
 *
 *   java -Djava.awt.headless=true -XX:+UnlockExperimentalVMOptions -XX:hashCode=2 \
 *        -XX:-OmitStackTraceInFastThrow -cp <original classes>:<dir of Harness.class> Harness [options] [cases.json|-]
 *
 * Options:
 *   --meta            print a JSON object describing the harness and the JVM, then exit
 *   --burn-seed N     before every case, compute k = f(case id, N) in [0, 96] identity
 *                     hash codes of throw-away objects. This only changes anything under
 *                     hash settings that keep state (hashCode=3/5); it is used to probe
 *                     other HashSet iteration orders. It has no effect under hashCode=2.
 *   --force-generic   build every map with the generic builder, also "star_fig2"
 *                     (used to check the builder against DetectiveGame.getBasicGame()).
 *
 * Input: a JSON array of cases, or an object {"maps": [...] | {...}, "cases": [...]}.
 * Output (stdout): a JSON array with one result object per case, one per line.
 *
 * Java 1.5 source level, no external libraries (the JSON reader/writer is below).
 * The case format follows tests/fixtures/README.md plus an "op" field; see
 * tools/reference/README.md ("Harness") for the list of ops and fields.
 */

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.FileInputStream;
import java.io.PrintStream;
import java.lang.management.ManagementFactory;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.Vector;

import common.util.IDGenerator;
import projects.cyberDetective.Algorithms;
import projects.cyberDetective.BeamDetector;
import projects.cyberDetective.DetectiveGame;
import projects.cyberDetective.Edge;
import projects.cyberDetective.Graph;
import projects.cyberDetective.ObservationHistory;
import projects.cyberDetective.OccupancySensor;
import projects.cyberDetective.Sensor;
import projects.cyberDetective.SensorRecording;
import projects.cyberDetective.Story;
import projects.cyberDetective.Vertex;
import projects.cyberDetective.ui.Environment;
import projects.cyberDetective.ui.Geometry;

public class Harness {

    public static final String VERSION = "1.1.0";

    /** Thrown for malformed cases (a harness problem, not original behaviour). */
    static class HarnessError extends RuntimeException {
        private static final long serialVersionUID = 1L;
        HarnessError(String m) { super(m); }
    }

    static boolean forceGeneric = false;
    static long burnSeed = -1;

    // =====================================================================
    // main
    // =====================================================================

    public static void main(String[] args) throws Exception {
        String file = "-";
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if (a.equals("--meta")) { System.out.println(Json.write(meta())); return; }
            else if (a.equals("--burn-seed")) { burnSeed = Long.parseLong(args[++i]); }
            else if (a.equals("--force-generic")) { forceGeneric = true; }
            else if (a.startsWith("--")) { throw new IllegalArgumentException("unknown option " + a); }
            else { file = a; }
        }
        InputStream in = file.equals("-") ? System.in : new FileInputStream(file);
        String text = readAll(in);
        Object root = Json.parse(text);

        Map<String, Map<String, Object>> maps = new LinkedHashMap<String, Map<String, Object>>();
        List<Object> cases;
        if (root instanceof List) {
            cases = asList(root);
        } else {
            Map<String, Object> r = asMap(root);
            Object ms = r.get("maps");
            if (ms instanceof List) {
                for (Object m : asList(ms)) maps.put(str(asMap(m).get("name")), asMap(m));
            } else if (ms instanceof Map) {
                for (Map.Entry<String, Object> e : asMap(ms).entrySet()) maps.put(e.getKey(), asMap(e.getValue()));
            }
            cases = asList(r.get("cases"));
        }

        PrintStream realOut = System.out;
        StringBuilder out = new StringBuilder();
        out.append("[\n");
        for (int i = 0; i < cases.size(); i++) {
            Map<String, Object> c = asMap(cases.get(i));
            Map<String, Object> res = runCase(c, maps);
            out.append(Json.write(res));
            out.append(i + 1 < cases.size() ? ",\n" : "\n");
        }
        out.append("]\n");
        System.setOut(realOut);
        PrintStream ps = new PrintStream(realOut, true, "UTF-8");
        ps.print(out.toString());
        ps.flush();
    }

    static Map<String, Object> meta() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("harness_version", VERSION);
        String[] props = {"java.version", "java.runtime.version", "java.vm.name", "java.vm.version",
                "java.vendor", "os.arch"};
        for (int i = 0; i < props.length; i++) m.put(props[i], System.getProperty(props[i]));
        List<Object> jvmArgs = new ArrayList<Object>();
        for (String s : ManagementFactory.getRuntimeMXBean().getInputArguments()) jvmArgs.add(s);
        m.put("jvm_input_arguments", jvmArgs);
        m.put("identity_hash_of_two_objects",
                Long.valueOf(System.identityHashCode(new Object())) + "," + System.identityHashCode(new Object()));
        return m;
    }

    // =====================================================================
    // One case: fresh game, captured stdout/stderr, exceptions recorded
    // =====================================================================

    static Map<String, Object> runCase(Map<String, Object> c, Map<String, Map<String, Object>> maps) {
        Map<String, Object> res = new LinkedHashMap<String, Object>();
        String id = c.get("id") == null ? null : str(c.get("id"));
        String op = c.get("op") == null ? null : str(c.get("op"));
        res.put("id", id);
        res.put("op", op);

        if (burnSeed >= 0) {
            int h = (id == null ? 0 : id.hashCode());
            long k = ((h * 31L + burnSeed * 1000003L) & 0x7fffffffL) % 97L;
            for (long i = 0; i < k; i++) System.identityHashCode(new Object());
        }

        PrintStream realOut = System.out;
        PrintStream realErr = System.err;
        ByteArrayOutputStream outBuf = new ByteArrayOutputStream();
        ByteArrayOutputStream errBuf = new ByteArrayOutputStream();
        Object ret = null;
        Map<String, Object> exc = null;
        String harnessError = null;
        try {
            PrintStream cap = new PrintStream(outBuf, true, "UTF-8");
            PrintStream capErr = new PrintStream(errBuf, true, "UTF-8");
            System.setOut(cap);
            System.setErr(capErr);
            try {
                ret = dispatch(op, c, maps, res);
            } catch (HarnessError e) {
                harnessError = e.getMessage();
            } catch (Throwable t) {
                exc = describe(t);
            }
            cap.flush();
            capErr.flush();
        } catch (IOException e) {
            harnessError = "capture failed: " + e;
        } finally {
            System.setOut(realOut);
            System.setErr(realErr);
        }
        res.put("return", ret);
        res.put("stdout", utf8(outBuf));
        res.put("exception", exc);
        String err = utf8(errBuf);
        if (err.length() > 0) res.put("stderr", err);
        if (harnessError != null) res.put("harness_error", harnessError);
        return res;
    }

    static Map<String, Object> describe(Throwable t) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("class", t.getClass().getName());
        m.put("message", t.getMessage());
        StackTraceElement[] st = t.getStackTrace();
        m.put("top_frame", st.length > 0 ? frame(st[0]) : null);
        String origin = null;
        List<Object> trace = new ArrayList<Object>();
        for (int i = 0; i < st.length; i++) {
            String cls = st[i].getClassName();
            if (cls.equals("Harness") || cls.startsWith("Harness$")) break;
            trace.add(frame(st[i]));
            if (origin == null && (cls.startsWith("projects.") || cls.startsWith("common."))) origin = frame(st[i]);
        }
        m.put("origin_frame", origin);
        m.put("trace", trace);
        return m;
    }

    static String frame(StackTraceElement e) {
        return e.getClassName() + "." + e.getMethodName() + "(" + e.getFileName() + ":" + e.getLineNumber() + ")";
    }

    // =====================================================================
    // Ops
    // =====================================================================

    static Object dispatch(String op, Map<String, Object> c, Map<String, Map<String, Object>> maps,
                           Map<String, Object> res) throws Exception {
        if (op == null) throw new HarnessError("case has no op");

        if (op.equals("algorithms_test")) {
            String name = str(c.get("name"));
            if (name.equals("testGraphRoutines")) Algorithms.testGraphRoutines();
            else if (name.equals("testStoryHistory")) Algorithms.testStoryHistory();
            else if (name.equals("testSingleAgent")) Algorithms.testSingleAgent();
            else if (name.equals("testMultiAgent")) Algorithms.testMultiAgent();
            else if (name.equals("main")) Algorithms.main(new String[0]);
            else throw new HarnessError("unknown algorithms_test " + name);
            return null;
        }
        if (op.equals("applet")) {
            return runApplet(c, res);
        }
        if (op.equals("builder_check")) {
            return builderCheck(c, maps);
        }

        Setup s = setup(c, maps);
        Graph g = s.game.graph;
        Vertex sv = g.vertexNameMap.get("SV");

        if (op.equals("validateAgentStory")) {
            return Boolean.valueOf(Algorithms.validateAgentStory(g, sv, s.game.story, s.game.obHis));
        }
        if (op.equals("validateAgentStoryMulti")) {
            return Boolean.valueOf(Algorithms.validateAgentStoryMulti(g, sv, s.game.story, s.game.obHis));
        }
        if (op.equals("getAgentStory")) {
            return Algorithms.getAgentStory(g, sv, s.game.story, s.game.obHis);
        }
        if (op.equals("getAgentStoryStatuses")) {
            Set<Vertex>[][] S = Algorithms.getAgentStoryStatuses(g, sv, s.game.story, s.game.obHis);
            if (S == null) return null;
            List<Object> aliases = new ArrayList<Object>();
            for (int i = 0; i < S.length; i++)
                for (int j = 0; j < i; j++)
                    if (S[i] == S[j]) { List<Object> p = new ArrayList<Object>(); p.add(Integer.valueOf(j)); p.add(Integer.valueOf(i)); aliases.add(p); break; }
            res.put("aliases", aliases);
            List<Object> phases = new ArrayList<Object>();
            for (int i = 0; i < S.length; i++) phases.add(encSetArray(S[i]));
            return phases;
        }
        if (op.equals("getSubGraph")) {
            Vertex v = g.vertexNameMap.get(str(c.get("s")));
            Vertex[] stv = c.containsKey("story_vertices") ? vertices(g, c.get("story_vertices"))
                    : s.game.story.getVertexSetAsArray();
            Vertex[] vg = vertices(g, c.get("goals"));
            Graph gp = Algorithms.getSubGraph(g, v, stv, vg);
            putDump(res, gp);
            return encGraph(gp);
        }
        if (op.equals("getSubGraphMulti")) {
            Vertex v = g.vertexNameMap.get(str(c.get("s")));
            Vertex[] occ = vertices(g, c.get("occupancy_active"));
            Vertex[] stv = c.containsKey("story_vertices") ? vertices(g, c.get("story_vertices"))
                    : s.game.story.getVertexSetAsArray();
            Vertex[] vg = vertices(g, c.get("goals"));
            Graph gp = Algorithms.getSubGraphMulti(g, v, occ, stv, vg);
            putDump(res, gp);
            return encGraph(gp);
        }
        if (op.equals("getReachableSubgraph")) {
            Vertex v = g.vertexNameMap.get(str(c.get("s")));
            Set<Vertex> vp = new HashSet<Vertex>();
            Vertex[] vps = vertices(g, c.get("vp_set"));
            for (int i = 0; i < vps.length; i++) vp.add(vps[i]);
            Vertex[] vg = vertices(g, c.get("goals"));
            Graph gp = Algorithms.getReachableSubgraph(g, v, vp, vg);
            putDump(res, gp);
            return encGraph(gp);
        }
        if (op.equals("game_dump")) {
            // Graph.dump(), Story.dump(), ObservationHistory.dump() as Algorithms.testStoryHistory does
            g.dump();
            s.game.story.dump();
            s.game.obHis.dump();
            return encGame(s.game);
        }
        if (op.equals("update_starting_vertex")) {
            // DetectiveGame.updateStartingVertex(vertexNameMap.get(v)); the game state is
            // described even when the call throws (it can leave the game corrupted).
            Object arg = c.get("vertex");
            Vertex v = arg == null ? null : g.vertexNameMap.get(str(arg));
            try {
                s.game.updateStartingVertex(v);
            } catch (Throwable t) {
                res.put("call_exception", describe(t));
            }
            putDump(res, g);
            return encGame(s.game);
        }
        throw new HarnessError("unknown op " + op);
    }

    // =====================================================================
    // Game construction
    // =====================================================================

    static class Setup {
        DetectiveGame game;
        Map<String, Object> mapSpec; // null for built-in games
    }

    /**
     * Builds a fresh game for a case. Either "game" names one of the four built-in
     * DetectiveGame games (story and history come from the game; default start
     * "fixed", i.e. SV stays joined to A as in Algorithms.test*), or "map" names a
     * map whose story/history are taken from the case (default start "story", i.e.
     * updateStartingVertex(first story room) as CyberDetectiveDemoApplet.java:235/265).
     */
    static Setup setup(Map<String, Object> c, Map<String, Map<String, Object>> maps) {
        Setup s = new Setup();
        String start;
        if (c.get("game") != null) {
            String gn = str(c.get("game"));
            if (gn.equals("SingleInfeasible")) s.game = DetectiveGame.getSingleInfeasibleGame();
            else if (gn.equals("SingleFeasible")) s.game = DetectiveGame.getSingleFeasibleGame();
            else if (gn.equals("MultiFeasible")) s.game = DetectiveGame.getMultiFeasibleGame();
            else if (gn.equals("MultiInfeasible")) s.game = DetectiveGame.getMultiInfeasibleGame();
            else if (gn.equals("Basic")) s.game = DetectiveGame.getBasicGame();
            else throw new HarnessError("unknown game " + gn);
            start = c.get("start") == null ? "fixed" : str(c.get("start"));
            s.mapSpec = starSpec();
        } else {
            Object mo = c.get("map");
            if (mo == null) throw new HarnessError("case has neither map nor game");
            Map<String, Object> spec;
            if (mo instanceof Map) spec = asMap(mo);
            else {
                String mn = str(mo);
                spec = maps.get(mn);
                if (spec == null && mn.equals("star_fig2")) spec = starSpec();
                if (spec == null) throw new HarnessError("unknown map " + mn);
            }
            spec = expandMap(spec);
            s.mapSpec = spec;
            boolean useBasic = "star_fig2".equals(spec.get("name")) && !forceGeneric
                    && !"generic".equals(c.get("builder"));
            s.game = useBasic ? DetectiveGame.getBasicGame() : buildGame(spec);
            Graph g = s.game.graph;
            List<Object> story = c.get("story") == null ? new ArrayList<Object>() : asList(c.get("story"));
            for (Object n : story) s.game.story.addVertex(g.vertexNameMap.get(str(n)));
            if (c.get("history") != null) addHistory(s.game, spec, asList(c.get("history")));
            start = c.get("start") == null ? "story" : str(c.get("start"));
        }
        if (start.equals("story")) {
            Vertex[] p = s.game.story.getStoryAsArray();
            // With an empty story there is no first room; SV stays where the map put it.
            if (p.length > 0) s.game.updateStartingVertex(p[0]);
        } else if (!start.equals("fixed")) {
            throw new HarnessError("unknown start " + start);
        }
        return s;
    }

    /** History pairs [sensor, "A"|"D"]; one Sensor object per sensor name, as the original. */
    static void addHistory(DetectiveGame game, Map<String, Object> spec, List<Object> hist) {
        spec = expandMap(spec);
        Graph g = game.graph;
        Map<String, Sensor> sensors = new HashMap<String, Sensor>();
        Map<String, Object> beams = spec.get("beams") == null ? new LinkedHashMap<String, Object>() : asMap(spec.get("beams"));
        List<Object> occ = spec.get("occupancy") == null ? new ArrayList<Object>() : asList(spec.get("occupancy"));
        for (Object o : hist) {
            List<Object> pr = asList(o);
            String name = str(pr.get(0));
            String ev = str(pr.get(1));
            Sensor sn = sensors.get(name);
            if (sn == null) {
                if (beams.containsKey(name)) {
                    List<Object> sides = asList(beams.get(name));
                    // as DetectiveGame.java:195-196 / CyberDetectiveDemoApplet.java:210-211
                    sn = new BeamDetector(name, new Vertex[]{g.vertexNameMap.get(str(sides.get(0))),
                            g.vertexNameMap.get(str(sides.get(1)))});
                } else if (occ.contains(name)) {
                    Vertex v = g.vertexNameMap.get(name);
                    if (v == null) throw new HarnessError("occupancy vertex missing: " + name);
                    sn = new OccupancySensor(v);
                } else {
                    throw new HarnessError("unknown sensor " + name);
                }
                sensors.put(name, sn);
            }
            int e;
            if (ev.equals("A")) e = SensorRecording.ACTIVATION;
            else if (ev.equals("D")) e = SensorRecording.DEACTIVATION;
            else throw new HarnessError("unknown event " + ev);
            game.obHis.addSensorRecording(new SensorRecording(sn, e));
        }
    }

    /**
     * Expands the compact map key "filler_occupancy": N (tests/fixtures/README.md): N edgeless
     * occupancy sensors "f1" ... "fN" are appended to "occupancy"; in "vertex_order" they
     * replace the single entry "..." (or are appended when there is none). With 65,535
     * fillers, the vertices after them get ids above 65535, where Edge.getEdgeId collides
     * (bug B7a). Returns spec itself when the key is absent; idempotent.
     */
    static Map<String, Object> expandMap(Map<String, Object> spec) {
        if (!spec.containsKey("filler_occupancy")) return spec;
        Object no = spec.get("filler_occupancy");
        if (!(no instanceof Long) || ((Long) no).longValue() < 0 || ((Long) no).longValue() > Integer.MAX_VALUE)
            throw new HarnessError("filler_occupancy must be a non-negative integer");
        int n = ((Long) no).intValue();
        List<Object> fill = new ArrayList<Object>(n);
        for (int i = 1; i <= n; i++) fill.add("f" + i);
        Map<String, Object> out = new LinkedHashMap<String, Object>(spec);
        out.remove("filler_occupancy");
        List<Object> occ = new ArrayList<Object>(strings(spec.get("occupancy")));
        occ.addAll(fill);
        out.put("occupancy", occ);
        if (spec.get("vertex_order") != null) {
            List<Object> vo = new ArrayList<Object>(strings(spec.get("vertex_order")));
            int k = vo.indexOf("...");
            if (k >= 0 && vo.lastIndexOf("...") != k)
                throw new HarnessError("vertex_order has more than one \"...\"");
            if (k >= 0) {
                vo.remove(k);
                vo.addAll(k, fill);
            } else {
                vo.addAll(fill);
            }
            out.put("vertex_order", vo);
        }
        return out;
    }

    /** The STAR Fig. 2 map in the form the generic builder reproduces getBasicGame() from. */
    static Map<String, Object> starSpec() {
        return asMap(Json.parse("{\"name\":\"star_fig2\",\"rooms\":[\"A\",\"B\",\"C\"],"
                + "\"beams\":{\"b1\":[\"b1u\",\"b1d\"],\"b2\":[\"b2r\",\"b2l\"]},\"occupancy\":[\"o1\",\"o2\"],"
                + "\"vertex_order\":[\"A\",\"B\",\"C\",\"b1u\",\"b1d\",\"b2l\",\"b2r\",\"o1\",\"o2\"],"
                + "\"edges\":[[\"A\",\"b1u\"],[\"A\",\"b1d\"],[\"A\",\"b2l\"],[\"A\",\"o1\"],[\"A\",\"C\"],"
                + "[\"B\",\"b2r\"],[\"C\",\"b1d\"],[\"C\",\"o1\"],[\"b1u\",\"b2l\"],[\"b2l\",\"o1\"],"
                + "[\"b1u\",\"o1\"],[\"b1d\",\"o1\"],[\"o1\",\"o2\"],[\"b2r\",\"o2\"],[\"B\",\"o2\"]]}"));
    }

    /**
     * Generic builder: replicates DetectiveGame.getBasicGame() (DetectiveGame.java:38-181)
     * step by step for an arbitrary map spec.
     *  - ids: a fresh IDGenerator whose first id is discarded (l.39-40); SV gets the next id
     *    (l.47), then the vertices in "vertex_order" (default: rooms, beam sides in listed
     *    order, occupancy), as l.48-56;
     *  - assoVertex for both sides of every beam (l.58-61); roomIds/beamIds/occuIds (l.63-71);
     *  - storyVertices = [SV] + rooms (l.73-76); sensorVertices = sensor vertices in
     *    vertex_order (l.77-82);
     *  - vertexIds, then vertexMap, then vertexNameMap, each filled in the order SV, rooms,
     *    beam sides (listed order), occupancy (l.88-119; note b2r before b2l there);
     *  - neighbour lists: vertex by vertex in id order (SV first), each vertex's neighbours in
     *    the order they appear in "edges"; SV is joined to rooms[0] and is the first neighbour
     *    of rooms[0] (l.122-162). The same addNeighbor call sequence as getBasicGame, so the
     *    identity hashes are even requested in the same order;
     *  - edges: built by iterating vertexMap.values() and each neighbour set (l.165-176).
     */
    static DetectiveGame buildGame(Map<String, Object> spec) {
        spec = expandMap(spec);
        List<String> rooms = strings(spec.get("rooms"));
        Map<String, Object> beams = spec.get("beams") == null ? new LinkedHashMap<String, Object>() : asMap(spec.get("beams"));
        List<String> occ = strings(spec.get("occupancy"));
        List<String> beamSides = new ArrayList<String>();
        for (Object sides : beams.values()) beamSides.addAll(strings(sides));
        List<String> order;
        if (spec.get("vertex_order") != null) order = strings(spec.get("vertex_order"));
        else {
            order = new ArrayList<String>(rooms);
            order.addAll(beamSides);
            order.addAll(occ);
        }
        Set<String> all = new HashSet<String>(rooms);
        all.addAll(beamSides);
        all.addAll(occ);
        if (all.size() != rooms.size() + beamSides.size() + occ.size() || all.contains("SV"))
            throw new HarnessError("duplicate or reserved vertex names in map");
        if (order.size() != all.size() || !all.containsAll(order))
            throw new HarnessError("vertex_order is not a permutation of the map's vertices");
        if (rooms.isEmpty()) throw new HarnessError("map has no rooms");

        IDGenerator idGen = new IDGenerator();
        idGen.getNextId();
        DetectiveGame game = new DetectiveGame();
        Graph g = new Graph();
        Vector<Vertex> stvs = new Vector<Vertex>();
        Vector<Vertex> sevs = new Vector<Vertex>();

        Map<String, Vertex> byName = new HashMap<String, Vertex>();
        Vertex sv = new Vertex("SV", idGen.getNextId());
        List<Vertex> created = new ArrayList<Vertex>();
        for (String n : order) {
            Vertex v = new Vertex(n, idGen.getNextId());
            byName.put(n, v);
            created.add(v);
        }
        for (Object sides : beams.values()) {
            List<String> sd = strings(sides);
            if (sd.size() != 2) throw new HarnessError("a beam needs exactly two sides");
            byName.get(sd.get(0)).assoVertex = byName.get(sd.get(1));
            byName.get(sd.get(1)).assoVertex = byName.get(sd.get(0));
        }
        for (String n : beamSides) game.beamIds.add(Integer.valueOf(byName.get(n).id));
        for (String n : rooms) game.roomIds.add(Integer.valueOf(byName.get(n).id));
        for (String n : occ) game.occuIds.add(Integer.valueOf(byName.get(n).id));

        stvs.add(sv);
        for (String n : rooms) stvs.add(byName.get(n));
        Set<String> sensorNames = new HashSet<String>(beamSides);
        sensorNames.addAll(occ);
        for (Vertex v : created) if (sensorNames.contains(v.name)) sevs.add(v);
        game.graph = g;
        game.storyVertices = stvs.toArray(new Vertex[0]);
        game.sensorVertices = sevs.toArray(new Vertex[0]);

        List<Vertex> mapOrder = new ArrayList<Vertex>();
        mapOrder.add(sv);
        for (String n : rooms) mapOrder.add(byName.get(n));
        for (String n : beamSides) mapOrder.add(byName.get(n));
        for (String n : occ) mapOrder.add(byName.get(n));
        for (Vertex v : mapOrder) g.vertexIds.add(Integer.valueOf(v.id));
        for (Vertex v : mapOrder) g.vertexMap.put(Integer.valueOf(v.id), v);
        for (Vertex v : mapOrder) g.vertexNameMap.put(v.name, v);

        // Neighbour lists keyed by (unique) vertex name: a HashMap keyed by Vertex would put
        // every vertex into one bin under hashCode=2 (all identity hashes equal), which is
        // quadratic on maps with 65,536+ vertices. The identity hashes are still requested
        // here, in id order, exactly as the former HashMap<Vertex, ...>.put calls did, so
        // stateful hash settings (hashCode=3/5) see the same sequence.
        Map<String, List<Vertex>> adjByName = new HashMap<String, List<Vertex>>();
        List<Vertex> idOrder = new ArrayList<Vertex>();
        idOrder.add(sv);
        idOrder.addAll(created);
        for (Vertex v : idOrder) {
            System.identityHashCode(v);
            adjByName.put(v.name, new ArrayList<Vertex>());
        }
        Vertex room0 = byName.get(rooms.get(0));
        adjByName.get(sv.name).add(room0);
        adjByName.get(room0.name).add(sv);
        for (Object e : asList(spec.get("edges"))) {
            List<String> pr = strings(e);
            Vertex u = byName.get(pr.get(0));
            Vertex w = byName.get(pr.get(1));
            if (u == null || w == null) throw new HarnessError("edge with unknown vertex " + pr);
            adjByName.get(u.name).add(w);
            adjByName.get(w.name).add(u);
        }
        for (Vertex v : idOrder) for (Vertex n : adjByName.get(v.name)) v.addNeighbor(n);

        Vertex[] vs = g.vertexMap.values().toArray(new Vertex[0]);
        for (int i = 0; i < vs.length; i++) {
            Vertex[] ns = vs[i].neighbors.toArray(new Vertex[0]);
            for (int j = 0; j < ns.length; j++) {
                if (!g.hasEdgeBetweenVertices(vs[i], ns[j])) {
                    Edge ed = new Edge(vs[i], ns[j]);
                    g.edgeIds.add(Integer.valueOf(ed.id));
                    g.edgeMap.put(Integer.valueOf(ed.id), ed);
                }
            }
        }
        game.story = new Story();
        game.obHis = new ObservationHistory();
        return game;
    }

    /** Compares the generic builder on the star_fig2 spec with DetectiveGame.getBasicGame(). */
    static Object builderCheck(Map<String, Object> c, Map<String, Map<String, Object>> maps) {
        Object mo = c.get("map");
        Map<String, Object> spec = mo instanceof Map ? asMap(mo)
                : (maps.get(str(mo)) != null ? maps.get(str(mo)) : starSpec());
        spec = expandMap(spec);
        DetectiveGame a = DetectiveGame.getBasicGame();
        DetectiveGame b = buildGame(spec);
        Map<String, Object> ra = structure(a);
        Map<String, Object> rb = structure(b);
        // neighbor_iteration_order is compared separately: it is the insertion order only
        // when all identity hashes are equal (hashCode=2); otherwise the two games' vertex
        // objects simply have different hashes.
        Object nioA = ra.remove("neighbor_iteration_order");
        Object nioB = rb.remove("neighbor_iteration_order");
        List<Object> diffs = new ArrayList<Object>();
        for (String k : ra.keySet()) {
            String x = Json.write(ra.get(k));
            String y = Json.write(rb.get(k));
            if (!x.equals(y)) diffs.add(k);
        }
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("identical", Boolean.valueOf(diffs.isEmpty()));
        m.put("differing_fields", diffs);
        m.put("compared_fields", new ArrayList<Object>(ra.keySet()));
        m.put("neighbor_iteration_order_equal", Boolean.valueOf(Json.write(nioA).equals(Json.write(nioB))));
        m.put("basic_game", ra);
        m.put("basic_game_neighbor_iteration_order", nioA);
        return m;
    }

    /** Everything about a game's construction that does not depend on identity hashes. */
    static Map<String, Object> structure(DetectiveGame game) {
        Graph g = game.graph;
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("vertexMap_order", namesOf(g.vertexMap.values()));
        List<Object> ids = new ArrayList<Object>();
        for (Vertex v : g.vertexMap.values()) ids.add(Integer.valueOf(v.id));
        m.put("vertexMap_ids", ids);
        m.put("vertexIds", sortedInts(g.vertexIds));
        m.put("vertexNameMap_keys", sortedStrings(g.vertexNameMap.keySet()));
        List<Object> es = new ArrayList<Object>();
        for (Edge e : g.edgeMap.values()) es.add(e.id + ":" + e.vertices[0].name + "--" + e.vertices[1].name);
        m.put("edgeMap_order_oriented", es);
        m.put("edgeIds", sortedInts(g.edgeIds));
        Map<String, Object> nb = new LinkedHashMap<String, Object>();
        Map<String, Object> asso = new LinkedHashMap<String, Object>();
        for (Vertex v : g.vertexMap.values()) {
            nb.put(v.name, sortedNames(v.neighbors));
            asso.put(v.name, v.assoVertex == null ? null : v.assoVertex.name);
        }
        m.put("neighbor_sets", nb);
        m.put("assoVertex", asso);
        m.put("roomIds", sortedInts(game.roomIds));
        m.put("beamIds", sortedInts(game.beamIds));
        m.put("occuIds", sortedInts(game.occuIds));
        m.put("storyVertices", namesOf(java.util.Arrays.asList(game.storyVertices)));
        m.put("sensorVertices", namesOf(java.util.Arrays.asList(game.sensorVertices)));
        // Iteration order of every neighbour set. Only comparable between two games when
        // identity hashes are equal (hashCode=2), where it is the insertion order.
        Map<String, Object> nbo = new LinkedHashMap<String, Object>();
        for (Vertex v : g.vertexMap.values()) nbo.put(v.name, namesOf(v.neighbors));
        m.put("neighbor_iteration_order", nbo);
        return m;
    }

    // =====================================================================
    // Applet replay (CyberDetectiveDemoApplet needs a display, so its non-drawing
    // logic is re-implemented here line by line; Environment is the original class)
    // =====================================================================

    static class AppletSim {
        Environment env;
        String storyText = "";
        String sensorText = "";
        String resultText = "";
        String instructions = "";
        boolean singleSelected = true; // l.121 singleButton.setSelected(true)
        Map<Integer, Vertex> vertexIdMap = new HashMap<Integer, Vertex>(); // l.48

        AppletSim() {
            // l.64: the environment (and its Rect/LineSegment vertex references) is
            // created once from a fresh basic game.
            env = Environment.createExampleEnvironment(DetectiveGame.getBasicGame());
            startSimulation(); // l.277
        }

        // l.281-288
        void startSimulation() {
            instructions = "Please pick a starting room from A, B, C to begin";
            DetectiveGame game = env.game;
            vertexIdMap.clear();
            vertexIdMap.put(Integer.valueOf(game.graph.vertexNameMap.get("A").id), game.graph.vertexNameMap.get("A"));
            vertexIdMap.put(Integer.valueOf(game.graph.vertexNameMap.get("B").id), game.graph.vertexNameMap.get("B"));
            vertexIdMap.put(Integer.valueOf(game.graph.vertexNameMap.get("C").id), game.graph.vertexNameMap.get("C"));
        }

        // l.182-190 (Reset button)
        void reset() {
            storyText = "";
            sensorText = "";
            resultText = "";
            env.game = DetectiveGame.getBasicGame();
            startSimulation();
        }

        // l.192-272 (Run Validation button). The step map receives the intermediate values.
        void run(Map<String, Object> step) {
            String story = storyText;                 // l.196
            String sensorString = sensorText;         // l.197
            if (story.length() == 0) {                // l.198-201
                resultText = "Nothing to validate.";
                return;
            }
            DetectiveGame game = env.game;            // l.202
            Graph g = game.graph;                     // l.203
            for (int i = 0; i < story.length(); i++) { // l.204-206: one vertex per CHARACTER
                game.story.addVertex(g.vertexNameMap.get(story.substring(i, i + 1)));
            }
            // l.208-211
            OccupancySensor O1 = new OccupancySensor(g.vertexNameMap.get("o1"));
            OccupancySensor O2 = new OccupancySensor(g.vertexNameMap.get("o2"));
            BeamDetector B1 = new BeamDetector("b1", new Vertex[]{g.vertexNameMap.get("b1u"), g.vertexNameMap.get("b1d")});
            BeamDetector B2 = new BeamDetector("b2", new Vertex[]{g.vertexNameMap.get("b2r"), g.vertexNameMap.get("b2l")});

            String[] sensors = sensorString.split(","); // l.213 (Java split: trailing empty strings dropped)

            if (singleSelected) {                     // l.215
                for (int i = 0; i < sensors.length; i++) { // l.216-233; other tokens are ignored
                    if (sensors[i].equals("o1")) {
                        game.obHis.addSensorRecording(new SensorRecording(O1, SensorRecording.ACTIVATION));
                        game.obHis.addSensorRecording(new SensorRecording(O1, SensorRecording.DEACTIVATION));
                    } else if (sensors[i].equals("o2")) {
                        game.obHis.addSensorRecording(new SensorRecording(O2, SensorRecording.ACTIVATION));
                        game.obHis.addSensorRecording(new SensorRecording(O2, SensorRecording.DEACTIVATION));
                    } else if (sensors[i].equals("b1")) {
                        game.obHis.addSensorRecording(new SensorRecording(B1, SensorRecording.ACTIVATION));
                    } else if (sensors[i].equals("b2")) {
                        game.obHis.addSensorRecording(new SensorRecording(B2, SensorRecording.ACTIVATION));
                    }
                }
                step.put("parsed_story", namesOrNull(game.story.visitedVertices));
                step.put("parsed_history", encHistory(game.obHis));
                game.updateStartingVertex(g.vertexNameMap.get(story.substring(0, 1))); // l.235
                boolean result = Algorithms.validateAgentStory(game.graph, g.vertexNameMap.get("SV"), game.story, game.obHis); // l.236
                step.put("validate", Boolean.valueOf(result));
                resultText = (result ? "Valid story." : "Inconsistent story."); // l.237
                if (result == true) {                 // l.238-241
                    String s = Algorithms.getAgentStory(game.graph, g.vertexNameMap.get("SV"), game.story, game.obHis);
                    step.put("path", s);
                    resultText = (result ? ("Valid story.\nA possible path: " + s) : "Inconsistent story.");
                }
            } else {
                boolean o1active = false;             // l.244-263: o1/o2 tokens toggle
                boolean o2active = false;
                for (int i = 0; i < sensors.length; i++) {
                    if (sensors[i].equals("o1")) {
                        o1active = !o1active;
                        game.obHis.addSensorRecording(new SensorRecording(O1, o1active ? SensorRecording.ACTIVATION : SensorRecording.DEACTIVATION));
                    } else if (sensors[i].equals("o2")) {
                        o2active = !o2active;
                        game.obHis.addSensorRecording(new SensorRecording(O2, o2active ? SensorRecording.ACTIVATION : SensorRecording.DEACTIVATION));
                    } else if (sensors[i].equals("b1")) {
                        game.obHis.addSensorRecording(new SensorRecording(B1, SensorRecording.ACTIVATION));
                    } else if (sensors[i].equals("b2")) {
                        game.obHis.addSensorRecording(new SensorRecording(B2, SensorRecording.ACTIVATION));
                    }
                }
                step.put("parsed_story", namesOrNull(game.story.visitedVertices));
                step.put("parsed_history", encHistory(game.obHis));
                game.updateStartingVertex(g.vertexNameMap.get(story.substring(0, 1))); // l.265
                boolean result = Algorithms.validateAgentStoryMulti(game.graph, g.vertexNameMap.get("SV"), game.story, game.obHis); // l.266
                step.put("validate", Boolean.valueOf(result));
                resultText = (result ? "Valid story." : "Inconsistent story."); // l.267
            }
            game.story.visitedVertices.clear();      // l.270-271 (skipped if anything above threw)
            game.obHis.sensorRecordings.clear();
        }

        // l.291-332 (mouseClicked); px/py are canvas pixel coordinates
        void click(int px, int py, Map<String, Object> step) {
            int x = (int) (px * Geometry.SCALING_FACTOR / Geometry.CANVAS_WIDTH); // l.292 (scalingFactor, canvasWidth: BasePanel l.15/21)
            int y = (int) (py * Geometry.SCALING_FACTOR / Geometry.CANVAS_WIDTH); // l.293 (also divides by canvasWidth)
            step.put("x", Integer.valueOf(x));
            step.put("y", Integer.valueOf(y));
            Vertex v = env.getClickedVertex(x, y);  // l.294
            step.put("hit", v == null ? null : v.name);
            boolean accepted = v != null && vertexIdMap.get(Integer.valueOf(v.id)) != null; // l.295
            step.put("accepted", Boolean.valueOf(accepted));
            if (accepted) {
                DetectiveGame game = env.game;
                if (game.roomIds.contains(Integer.valueOf(v.id))) { // l.298-300
                    storyText = storyText + v.name;
                } else {                              // l.301-315
                    String text = sensorText;
                    if (text.length() > 0) text = text + "," + v.name.substring(0, 2);
                    else text = v.name.substring(0, 2);
                    if (!game.occuIds.contains(Integer.valueOf(v.id))) v = v.assoVertex;
                    sensorText = text;
                }
                StringBuffer buf = new StringBuffer("Current location: "); // l.317-330
                buf.append(v.name);
                buf.append("\nReachable features: ");
                vertexIdMap.clear();
                Vertex[] ns = v.neighbors.toArray(new Vertex[0]);
                for (int i = 0; i < ns.length; i++) {
                    if (ns[i].name.equals("SV")) continue;
                    vertexIdMap.put(Integer.valueOf(ns[i].id), ns[i]);
                    buf.append(ns[i].name + ", ");
                }
                vertexIdMap.put(Integer.valueOf(v.id), v);
                buf.append(v.name);
                buf.append("\nPlease click on one of the above features or run validation.");
                instructions = buf.toString();
            }
        }
    }

    /**
     * op "applet": "steps" is a list of
     *   {"run": {"story": "...", "sensors": "...", "mode": "single"|"multi"}}
     *        (story/sensors set the text fields when present; mode sets the radio button)
     *   {"reset": true}
     *   {"click": [px, py]}           (canvas pixels, 400x300)
     * replayed against ONE applet instance, so state carried between steps (a story left
     * behind by a crash, a corrupted SV) is part of the result. Each step records its own
     * stdout and exception, as the Swing event thread would survive the exception.
     * A case may give "story"/"sensors"/"applet_mode" instead of "steps" for a single run.
     */
    static Object runApplet(Map<String, Object> c, Map<String, Object> res) throws Exception {
        List<Object> steps;
        if (c.get("steps") != null) steps = asList(c.get("steps"));
        else {
            Map<String, Object> run = new LinkedHashMap<String, Object>();
            run.put("story", c.get("story_text"));
            run.put("sensors", c.get("sensor_text"));
            run.put("mode", c.get("applet_mode"));
            Map<String, Object> st = new LinkedHashMap<String, Object>();
            st.put("run", run);
            steps = new ArrayList<Object>();
            steps.add(st);
        }
        AppletSim sim = new AppletSim();
        List<Object> out = new ArrayList<Object>();
        PrintStream caseOut = System.out;
        for (Object so : steps) {
            Map<String, Object> s = asMap(so);
            Map<String, Object> rec = new LinkedHashMap<String, Object>();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            PrintStream ps = new PrintStream(buf, true, "UTF-8");
            System.setOut(ps);
            try {
                if (s.containsKey("run")) {
                    Map<String, Object> r = asMap(s.get("run"));
                    rec.put("action", "run");
                    if (r.get("story") != null) sim.storyText = str(r.get("story"));
                    if (r.get("sensors") != null) sim.sensorText = str(r.get("sensors"));
                    if (r.get("mode") != null) {
                        String m = str(r.get("mode"));
                        if (m.equals("single")) sim.singleSelected = true;
                        else if (m.equals("multi")) sim.singleSelected = false;
                        else throw new HarnessError("unknown applet mode " + m);
                    }
                    rec.put("mode", sim.singleSelected ? "single" : "multi");
                    rec.put("story_text", sim.storyText);
                    rec.put("sensor_text", sim.sensorText);
                    rec.put("validate", null);
                    rec.put("path", null);
                    try {
                        sim.run(rec);
                        rec.put("exception", null);
                    } catch (HarnessError e) {
                        throw e;
                    } catch (Throwable t) {
                        rec.put("exception", describe(t));
                    }
                    rec.put("result_text", sim.resultText);
                    rec.put("game_story_after", namesOrNull(sim.env.game.story.visitedVertices));
                    rec.put("game_history_after", encHistory(sim.env.game.obHis));
                } else if (s.containsKey("reset")) {
                    rec.put("action", "reset");
                    try { sim.reset(); rec.put("exception", null); }
                    catch (Throwable t) { rec.put("exception", describe(t)); }
                } else if (s.containsKey("click")) {
                    List<Object> xy = asList(s.get("click"));
                    rec.put("action", "click");
                    rec.put("px", xy.get(0));
                    rec.put("py", xy.get(1));
                    try {
                        sim.click(((Number) xy.get(0)).intValue(), ((Number) xy.get(1)).intValue(), rec);
                        rec.put("exception", null);
                    } catch (Throwable t) {
                        rec.put("exception", describe(t));
                    }
                    rec.put("story_text", sim.storyText);
                    rec.put("sensor_text", sim.sensorText);
                    rec.put("instructions", sim.instructions);
                } else {
                    throw new HarnessError("unknown applet step " + Json.write(s));
                }
            } finally {
                ps.flush();
                System.setOut(caseOut);
            }
            rec.put("stdout", utf8(buf));
            out.add(rec);
        }
        return out;
    }

    // =====================================================================
    // Encoders
    // =====================================================================

    static Object encSetArray(Set<Vertex>[] a) {
        List<Object> l = new ArrayList<Object>();
        for (int i = 0; i < a.length; i++) l.add(sortedNames(a[i]));
        return l;
    }

    static Map<String, Object> encGraph(Graph G) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("vertices", sortedNames(G.vertexMap.values()));
        List<List<Object>> edges = new ArrayList<List<Object>>();
        for (Edge e : G.edgeMap.values()) {
            String a = nameOf(e.vertices[0]);
            String b = nameOf(e.vertices[1]);
            List<Object> p = new ArrayList<Object>();
            if (cmp(a, b) <= 0) { p.add(a); p.add(b); } else { p.add(b); p.add(a); }
            edges.add(p);
        }
        Collections.sort(edges, new Comparator<List<Object>>() {
            public int compare(List<Object> x, List<Object> y) {
                int r = cmp((String) x.get(0), (String) y.get(0));
                return r != 0 ? r : cmp((String) x.get(1), (String) y.get(1));
            }
        });
        m.put("edges", new ArrayList<Object>(edges));
        Map<String, Object> ids = new LinkedHashMap<String, Object>();
        Map<String, Object> adj = new LinkedHashMap<String, Object>();
        List<Vertex> vs = new ArrayList<Vertex>(G.vertexMap.values());
        Collections.sort(vs, new Comparator<Vertex>() {
            public int compare(Vertex x, Vertex y) { return cmp(nameOf(x), nameOf(y)); }
        });
        for (Vertex v : vs) {
            ids.put(v.name, Integer.valueOf(v.id));
            adj.put(v.name, sortedNames(v.neighbors));
        }
        m.put("ids", ids);
        m.put("adjacency", adj);
        return m;
    }

    static Map<String, Object> encGame(DetectiveGame game) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("graph", encGraph(game.graph));
        m.put("story", namesOrNull(game.story.visitedVertices));
        m.put("history", encHistory(game.obHis));
        m.put("structure", structure(game));
        return m;
    }

    static List<Object> encHistory(ObservationHistory oh) {
        List<Object> l = new ArrayList<Object>();
        for (SensorRecording r : oh.sensorRecordings) {
            List<Object> p = new ArrayList<Object>();
            p.add(r.sensor.name);
            p.add(r.event == SensorRecording.ACTIVATION ? "A" : (r.event == SensorRecording.DEACTIVATION ? "D" : String.valueOf(r.event)));
            l.add(p);
        }
        return l;
    }

    /**
     * res.graph_dump = what Graph.dump() prints for G (not part of the op's own stdout).
     * If dump() throws (e.g. a null neighbour), graph_dump holds the partial output and
     * graph_dump_exception the exception.
     */
    static void putDump(Map<String, Object> res, Graph G) throws IOException {
        PrintStream prev = System.out;
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        PrintStream ps = new PrintStream(buf, true, "UTF-8");
        System.setOut(ps);
        Throwable err = null;
        try {
            G.dump();
        } catch (Throwable t) {
            err = t;
        } finally {
            ps.flush();
            System.setOut(prev);
        }
        res.put("graph_dump", utf8(buf));
        if (err != null) res.put("graph_dump_exception", describe(err));
    }

    // =====================================================================
    // Small helpers
    // =====================================================================

    static String nameOf(Vertex v) { return v == null ? null : v.name; }

    static int cmp(String a, String b) {
        if (a == null) return b == null ? 0 : -1;
        if (b == null) return 1;
        return a.compareTo(b);
    }

    static List<Object> sortedNames(java.util.Collection<Vertex> vs) {
        List<String> l = new ArrayList<String>();
        for (Vertex v : vs) l.add(nameOf(v));
        Collections.sort(l, new Comparator<String>() {
            public int compare(String x, String y) { return cmp(x, y); }
        });
        return new ArrayList<Object>(l);
    }

    static List<Object> namesOf(java.util.Collection<Vertex> vs) {
        List<Object> l = new ArrayList<Object>();
        for (Vertex v : vs) l.add(nameOf(v));
        return l;
    }

    static List<Object> namesOrNull(java.util.Collection<Vertex> vs) { return namesOf(vs); }

    static List<Object> sortedInts(java.util.Collection<Integer> c) {
        List<Integer> l = new ArrayList<Integer>(c);
        Collections.sort(l);
        return new ArrayList<Object>(l);
    }

    static List<Object> sortedStrings(java.util.Collection<String> c) {
        List<String> l = new ArrayList<String>(c);
        Collections.sort(l);
        return new ArrayList<Object>(l);
    }

    static Vertex[] vertices(Graph g, Object names) {
        if (names == null) return new Vertex[0];
        List<Object> l = asList(names);
        Vertex[] a = new Vertex[l.size()];
        for (int i = 0; i < a.length; i++) a[i] = l.get(i) == null ? null : g.vertexNameMap.get(str(l.get(i)));
        return a;
    }

    static List<String> strings(Object o) {
        List<String> l = new ArrayList<String>();
        if (o == null) return l;
        for (Object x : asList(o)) l.add(str(x));
        return l;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object o) {
        if (!(o instanceof Map)) throw new HarnessError("expected a JSON object, got " + Json.write(o));
        return (Map<String, Object>) o;
    }

    @SuppressWarnings("unchecked")
    static List<Object> asList(Object o) {
        if (!(o instanceof List)) throw new HarnessError("expected a JSON array, got " + Json.write(o));
        return (List<Object>) o;
    }

    static String str(Object o) {
        if (!(o instanceof String)) throw new HarnessError("expected a JSON string, got " + Json.write(o));
        return (String) o;
    }

    static String utf8(ByteArrayOutputStream b) {
        try { return b.toString("UTF-8"); } catch (IOException e) { throw new RuntimeException(e); }
    }

    static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        int n;
        while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
        in.close();
        return utf8(b);
    }


    // =====================================================================
    // Minimal JSON reader / writer (objects -> LinkedHashMap, arrays -> ArrayList,
    // numbers -> Long or Double). Output is ASCII-only and key order is preserved.
    // =====================================================================

    static final class Json {
        private final String s;
        private int i;

        private Json(String s) { this.s = s; }

        static Object parse(String text) {
            Json p = new Json(text);
            p.ws();
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new HarnessError("trailing characters in JSON at " + p.i);
            return v;
        }

        private void ws() {
            while (i < s.length()) {
                char ch = s.charAt(i);
                if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') i++; else break;
            }
        }

        private Object value() {
            if (i >= s.length()) throw new HarnessError("unexpected end of JSON");
            char ch = s.charAt(i);
            if (ch == '{') {
                i++;
                Map<String, Object> m = new LinkedHashMap<String, Object>();
                ws();
                if (s.charAt(i) == '}') { i++; return m; }
                while (true) {
                    ws();
                    if (s.charAt(i) != '"') throw new HarnessError("expected key at " + i);
                    String k = string();
                    ws();
                    expect(':');
                    ws();
                    m.put(k, value());
                    ws();
                    if (s.charAt(i) == ',') { i++; continue; }
                    expect('}');
                    return m;
                }
            }
            if (ch == '[') {
                i++;
                List<Object> l = new ArrayList<Object>();
                ws();
                if (s.charAt(i) == ']') { i++; return l; }
                while (true) {
                    ws();
                    l.add(value());
                    ws();
                    if (s.charAt(i) == ',') { i++; continue; }
                    expect(']');
                    return l;
                }
            }
            if (ch == '"') return string();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            int st = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            String num = s.substring(st, i);
            if (num.length() == 0) throw new HarnessError("bad JSON value at " + st);
            if (num.indexOf('.') >= 0 || num.indexOf('e') >= 0 || num.indexOf('E') >= 0) return Double.valueOf(num);
            return Long.valueOf(num);
        }

        private void expect(char ch) {
            if (i >= s.length() || s.charAt(i) != ch) throw new HarnessError("expected '" + ch + "' at " + i);
            i++;
        }

        private String string() {
            expect('"');
            StringBuilder b = new StringBuilder();
            while (true) {
                char ch = s.charAt(i++);
                if (ch == '"') return b.toString();
                if (ch == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case '"': b.append('"'); break;
                        case '\\': b.append('\\'); break;
                        case '/': b.append('/'); break;
                        case 'b': b.append('\b'); break;
                        case 'f': b.append('\f'); break;
                        case 'n': b.append('\n'); break;
                        case 'r': b.append('\r'); break;
                        case 't': b.append('\t'); break;
                        case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                        default: throw new HarnessError("bad escape at " + i);
                    }
                } else {
                    b.append(ch);
                }
            }
        }

        static String write(Object o) {
            StringBuilder b = new StringBuilder();
            write(o, b);
            return b.toString();
        }

        @SuppressWarnings("unchecked")
        static void write(Object o, StringBuilder b) {
            if (o == null) b.append("null");
            else if (o instanceof String) quote((String) o, b);
            else if (o instanceof Boolean || o instanceof Integer || o instanceof Long) b.append(o.toString());
            else if (o instanceof Number) b.append(o.toString());
            else if (o instanceof Map) {
                b.append('{');
                boolean first = true;
                for (Iterator<Map.Entry<String, Object>> it = ((Map<String, Object>) o).entrySet().iterator(); it.hasNext(); ) {
                    Map.Entry<String, Object> e = it.next();
                    if (!first) b.append(',');
                    first = false;
                    quote(e.getKey(), b);
                    b.append(':');
                    write(e.getValue(), b);
                }
                b.append('}');
            } else if (o instanceof List) {
                b.append('[');
                boolean first = true;
                for (Object x : (List<Object>) o) {
                    if (!first) b.append(',');
                    first = false;
                    write(x, b);
                }
                b.append(']');
            } else {
                quote(String.valueOf(o), b);
            }
        }

        static void quote(String s, StringBuilder b) {
            b.append('"');
            for (int k = 0; k < s.length(); k++) {
                char ch = s.charAt(k);
                switch (ch) {
                    case '"': b.append("\\\""); break;
                    case '\\': b.append("\\\\"); break;
                    case '\n': b.append("\\n"); break;
                    case '\r': b.append("\\r"); break;
                    case '\t': b.append("\\t"); break;
                    case '\b': b.append("\\b"); break;
                    case '\f': b.append("\\f"); break;
                    default:
                        if (ch < 0x20 || ch > 0x7e) {
                            String h = Integer.toHexString(ch);
                            b.append("\\u");
                            for (int z = h.length(); z < 4; z++) b.append('0');
                            b.append(h);
                        } else {
                            b.append(ch);
                        }
                }
            }
            b.append('"');
        }
    }
}
