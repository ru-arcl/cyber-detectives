/*
 * Prints the iteration order of java.util.HashSet after every operation of a script, so
 * that cyber_detectives.compat.javahash can be checked against the real JDK
 * (see javahash_probe.py, which writes the script and tests/fixtures/golden/javahash.json).
 *
 *   java -XX:+UnlockExperimentalVMOptions -XX:hashCode=2 -cp <dir> JavaHashProbe < script
 *
 * Script lines (whitespace separated):
 *   new obj|int|str     start a new, empty HashSet (default constructor)
 *   + TOKEN             set.add(key)
 *   - TOKEN             set.remove(key)
 *   ihash               print System.identityHashCode of two fresh objects
 * Keys: for "obj" sets TOKEN names a distinct object without hashCode/equals (created on
 * first use, so identity hashes are requested in script order); for "int" sets it is a
 * decimal java.lang.Integer; for "str" sets it is the string itself. The token "null"
 * is the null key in every kind of set.
 * Output: one line per +/- operation, the set's iteration order (tokens separated by one
 * space; empty line for an empty set).
 *
 * Java 1.5 source level, no external libraries.
 */
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;

public class JavaHashProbe {

    /** A key class with identity hashCode/equals, like projects.cyberDetective.Vertex. */
    static final class Obj {
        final String label;
        Obj(String label) { this.label = label; }
        public String toString() { return label; }
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, "UTF-8"));
        PrintStream out = new PrintStream(System.out, false, "UTF-8");
        StringBuilder buf = new StringBuilder();
        HashSet<Object> set = null;
        String kind = null;
        Map<String, Obj> objs = new HashMap<String, Obj>();
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.length() == 0) continue;
            String[] t = line.split("\\s+");
            if (t[0].equals("new")) {
                kind = t[1];
                set = new HashSet<Object>();
                objs = new HashMap<String, Obj>();
                continue;
            }
            if (t[0].equals("ihash")) {
                buf.append(System.identityHashCode(new Object())).append(' ')
                   .append(System.identityHashCode(new Object())).append('\n');
                continue;
            }
            Object key;
            if (t[1].equals("null")) key = null;
            else if (kind.equals("obj")) {
                Obj o = objs.get(t[1]);
                if (o == null) { o = new Obj(t[1]); System.identityHashCode(o); objs.put(t[1], o); }
                key = o;
            } else if (kind.equals("int")) key = Integer.valueOf(t[1]);
            else key = t[1];
            if (t[0].equals("+")) set.add(key);
            else if (t[0].equals("-")) set.remove(key);
            else throw new IllegalArgumentException("bad line: " + line);
            boolean first = true;
            for (Iterator<Object> it = set.iterator(); it.hasNext(); ) {
                Object k = it.next();
                if (!first) buf.append(' ');
                first = false;
                buf.append(k == null ? "null" : k.toString());
            }
            buf.append('\n');
        }
        out.print(buf.toString());
        out.flush();
    }
}
