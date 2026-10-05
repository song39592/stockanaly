using System;
using System.Collections.Generic;
using System.Globalization;

namespace StockPool
{
    /// <summary>
    /// JSON 取值与格式化工具（第 02 项）。
    ///
    /// 各页原本各写一套私有小函数：4 份逐字等价的取键、6 份转 double、3 份转 int、
    /// 4 份转 string、4 份百分比。**实现逐字沿用原函数体**，只把重复的收在一起，行为不变。
    ///
    /// 三处**故意不合并**（合并就是改口径，不是去重）：
    ///   1. NumOrNull 返回 double?（估值页靠 null 显示「—」），与 NumOr / Num 是不同语义；
    ///   2. PctRaw **不乘 100** —— 盘面涨跌幅本身已是百分数，乘 100 会静默放大 100 倍；
    ///   3. 深浅双份（筹码峰获利/套牢）同理，见 Palette.cs。
    ///
    /// 一处有意的口径统一：转 double 统一用 InvariantCulture（原先只有下载页这么做）。
    /// JSON 数字与数字字符串一律以 . 作小数点，与区域性格式无关；zh-CN 下行为完全相同。
    /// </summary>
    internal static class J
    {
        // ---------------- 取值 ----------------

        /// <summary>按 key 取值；字典为 null 或缺 key 都返回 null。</summary>
        public static object Get(Dictionary<string, object> d, string k)
        {
            if (d == null) return null;
            object v;
            if (d.TryGetValue(k, out v)) return v;
            return null;
        }

        /// <summary>同 Get，但容器是 object（内部先 as 成字典）。</summary>
        public static object GetFrom(object container, string key)
        {
            var dict = container as Dictionary<string, object>;
            if (dict == null) return null;
            object value;
            return dict.TryGetValue(key, out value) ? value : null;
        }

        /// <summary>转字典；不是字典就返回 null。</summary>
        public static Dictionary<string, object> Map(object o)
        {
            return o as Dictionary<string, object>;
        }

        /// <summary>转数组：兼容 ArrayList 与 object[]（反序列化两种都可能出现）。</summary>
        public static System.Collections.ArrayList Arr(object o)
        {
            if (o == null) return null;
            if (o is System.Collections.ArrayList) return (System.Collections.ArrayList)o;
            if (o is object[])
            {
                var arr = (object[])o;
                var al = new System.Collections.ArrayList();
                foreach (object x in arr) al.Add(x);
                return al;
            }
            return null;
        }

        // ---------------- 转数值 ----------------

        /// <summary>转 double，失败返回 null（调用方据此显示「—」）。</summary>
        public static double? NumOrNull(object o)
        {
            if (o == null) return null;
            if (o is double) return (double)o;
            // JavaScriptSerializer 会把 JSON 小数反序列化成 decimal，必须显式处理
            if (o is decimal) return Convert.ToDouble(o);
            if (o is float) return Convert.ToDouble(o);
            if (o is int) return (int)o;
            if (o is long) return (long)o;
            if (o is string)
            {
                double dv;
                if (double.TryParse((string)o, out dv)) return dv;
            }
            return null;
        }

        /// <summary>转 double，失败返回调用方给的默认值。</summary>
        public static double NumOr(object o, double d)
        {
            if (o == null) return d;
            try { return Convert.ToDouble(o, CultureInfo.InvariantCulture); } catch { return d; }
        }

        /// <summary>转 double，失败返回 0。</summary>
        public static double Num(object o)
        {
            if (o == null) return 0;
            try { return Convert.ToDouble(o, CultureInfo.InvariantCulture); } catch { return 0; }
        }

        /// <summary>转 int，失败返回 0。</summary>
        public static int Int(object o)
        {
            try { return Convert.ToInt32(o); } catch { return 0; }
        }

        // ---------------- 转文本 ----------------

        /// <summary>转字符串，null 转空串。</summary>
        public static string Str(object o)
        {
            if (o == null) return "";
            return Convert.ToString(o);
        }

        /// <summary>转字符串，null 转调用方给的默认值。</summary>
        public static string StrOr(object o, string d)
        {
            return o == null ? d : o.ToString();
        }

        /// <summary>按 key 取值再转字符串，缺失转空串。</summary>
        public static string StrAt(Dictionary<string, object> j, string k)
        {
            return Str(Get(j, k));
        }

        /// <summary>按 key 取值再转 double，缺失或失败转 0。</summary>
        public static double NumAt(Dictionary<string, object> j, string k)
        {
            return Num(Get(j, k));
        }

        // ---------------- 百分比（三个口径，别混用）----------------

        /// <summary>带符号百分数：v 是小数（0.1 转 +10.00%）。</summary>
        public static string Pct100(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + (v.Value * 100).ToString("F2") + "%";
        }

        /// <summary>不带符号百分数：0.1 转 10.00%（贴现率 / 永续增长等口径值）。</summary>
        public static string Pct100Plain(double? v)
        {
            if (v == null) return "—";
            return (v.Value * 100).ToString("F2") + "%";
        }

        /// <summary>带符号百分数但**不乘 100**：盘面接口已是百分数，乘 100 会放大 100 倍。</summary>
        public static string PctRaw(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + v.Value.ToString("F2") + "%";
        }

        // ---------------- 数字格式化 ----------------

        /// <summary>定点小数，null 转「—」。</summary>
        public static string Fmt(double? v, int d = 2)
        {
            if (v == null) return "—";
            return v.Value.ToString("F" + d);
        }

        /// <summary>定点小数（入参 object），无法解析转「—」。</summary>
        public static string FmtNum(object o, int d = 2)
        {
            return Fmt(NumOrNull(o), d);
        }

        /// <summary>金额：万以上取整并加千分位。</summary>
        public static string Money(double v)
        {
            double a = Math.Abs(v);
            string s = (a >= 10000) ? a.ToString("N0") : a.ToString("F2");
            return (v < 0 ? "-¥" : "¥") + s;
        }

        /// <summary>「亿」金额，不带符号，入参 object。</summary>
        public static string Yi(object o)
        {
            double? v = NumOrNull(o);
            return v == null ? "—" : v.Value.ToString("F2") + " 亿";
        }

        /// <summary>「亿」金额，带符号。</summary>
        public static string YiSigned(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + v.Value.ToString("F2") + " 亿";
        }
    }
}
