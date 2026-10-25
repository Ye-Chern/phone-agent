package com.moye.phoneagent;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.Editable;
import android.text.SpannableString;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.TextWatcher;
import android.text.style.BackgroundColorSpan;
import android.text.style.StyleSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.ForegroundColorSpan;
import android.text.style.BulletSpan;
import android.text.style.URLSpan;
import android.text.method.LinkMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.util.Base64;
import android.widget.ImageView;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.List;

public class MainActivity extends Activity {

    static final String BASE = "http://127.0.0.1:8756";
    static final int REQ_PICK_IMG = 1001;
    static final int BG = Color.parseColor("#0b0e14");
    static final int PANEL = Color.parseColor("#12161f");
    static final int CARD = Color.parseColor("#171c28");
    static final int BORDER = Color.parseColor("#242b3a");
    static final int TEXT = Color.parseColor("#e6e9f0");
    static final int DIM = Color.parseColor("#8b93a7");
    static final int ACCENT = Color.parseColor("#4f8cff");
    static final int GREEN = Color.parseColor("#22c55e");
    static final int RED = Color.parseColor("#f87171");
    static final int USER_BG = Color.parseColor("#1d3a6f");

    LinearLayout msgList;
    ScrollView scroller;
    EditText input;
    Button sendBtn, stopBtn, pickBtn, menuBtn, newConvBtn;
    Spinner modelSel, reasoningSel;
    String pendingImg = null;
    TextView convTitle, statusDot, sbText;
    TextView chatgptPlanHint;
    LinearLayout sleepBanner;
    int failCount = 0;
    volatile boolean waking = false;
    volatile long wakeStart = 0;
    volatile long lastHeal = 0;
    volatile String curConv = null;
    volatile boolean busy = false;
    volatile HttpURLConnection streamConn = null;
    volatile StreamCtx activeStream = null;
    volatile boolean stopping = false;
    boolean creatingConversation = false;
    boolean preparingSend = false;
    boolean modeSwitching = false;
    String modeSwitchTarget = null, modeSwitchPrevious = null, modeSwitchPreviousConv = null;
    String modeSwitchPreviousDraft = "";
    int modeSwitchGeneration = 0;
    volatile boolean stick = true;
    boolean suppressScroll = false;
    boolean userOnly = false;
    Button filterBtn, jumpBtn;
    Button modeBtn, headerToggle;
    LinearLayout modelRow;
    boolean headerCollapsed = false, restoringReasoning = false, reasoningGesture = false;
    String reasoningGestureModel = "", currentReasoningEffort = "";
    int reasoningRestoreGeneration = 0;
    String currentMode = "work";
    String currentWorkdir = "";
    boolean currentMemoryEnabled = true;
    int contextEstimate = 0, contextBudget = 0;
    String currentIdentity = "", currentScene = "", currentPerspective = "";
    JSONObject statusSnapshot = new JSONObject();
    JSONObject modelRegistry = new JSONObject();
    boolean restoringModel = false;
    boolean modelGesture = false;
    String modelGestureStart = "";
    int modelRestoreGeneration = 0;
    List<String> models = new ArrayList<String>();
    List<String> visionModels = new ArrayList<String>();
    List<String> modelLabels = new ArrayList<String>();
    String modelSignature = "";
    List<ConvInfo> convs = new ArrayList<ConvInfo>();
    final android.os.Handler mainHandler = new android.os.Handler();
    Runnable statusPoll;
    volatile long viewGeneration = 0;
    volatile boolean activityResumed = false;
    String failedConv = null;
    String failedModel = null;
    LinearLayout lastFailureBubble;

    static class ConvInfo {
        String id, title, mode; int cnt; long updated;
        long ts() { return updated * 1000L; }
        public String toString() { return title + " (" + cnt + ")"; }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        refreshStatus();
        refreshConvs(false);
        maybeOfferSetup();
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityResumed = true;
        scheduleStatusPoll(1000);
    }

    @Override
    protected void onPause() {
        activityResumed = false;
        if (statusPoll != null) mainHandler.removeCallbacks(statusPoll);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        activityResumed = false;
        if (statusPoll != null) mainHandler.removeCallbacks(statusPoll);
        ++viewGeneration;
        try { if (streamConn != null) streamConn.disconnect(); } catch (Exception ignored) {}
        super.onDestroy();
    }

    void scheduleStatusPoll(long delay) {
        if (statusPoll != null) mainHandler.removeCallbacks(statusPoll);
        statusPoll = new Runnable() {
            public void run() {
                if (!activityResumed || isFinishing()) return;
                refreshStatus();
                mainHandler.postDelayed(this, waking ? 1000 : 5000);
            }
        };
        mainHandler.postDelayed(statusPoll, delay);
    }

    // ---------------- UI 构建 ----------------

    void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        LinearLayout main = new LinearLayout(this);
        main.setOrientation(LinearLayout.VERTICAL);

        // 顶栏分成标题行和模型行，避免窄屏上控件互相挤压。
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setBackgroundColor(PANEL);
        header.setPadding(dp(10), dp(7), dp(10), dp(7));
        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        menuBtn = smallBtn("☰");
        menuBtn.setContentDescription("打开会话侧边栏");
        menuBtn.setOnClickListener(v -> showConvsDialog());
        titleRow.addView(menuBtn, new LinearLayout.LayoutParams(dp(44), dp(44)));
        LinearLayout titleCol = new LinearLayout(this);
        titleCol.setOrientation(LinearLayout.VERTICAL);
        titleCol.setPadding(dp(8), 0, 0, 0);
        convTitle = new TextView(this);
        convTitle.setText("PhoneAgent");
        convTitle.setTextColor(TEXT); convTitle.setTextSize(16);
        statusDot = new TextView(this);
        statusDot.setText("● 连接中…"); statusDot.setTextSize(10); statusDot.setTextColor(DIM);
        titleCol.addView(convTitle); titleCol.addView(statusDot);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        titleRow.addView(titleCol, tp);
        modeBtn = smallBtn("Work ▾");
        modeBtn.setContentDescription("切换工作模式或聊天模式");
        modeBtn.setOnClickListener(v -> chooseMode());
        titleRow.addView(modeBtn, new LinearLayout.LayoutParams(dp(92), dp(42)));
        headerToggle = smallBtn("⌃");
        headerToggle.setContentDescription("收起顶部工具栏");
        headerToggle.setOnClickListener(v -> setHeaderCollapsed(!headerCollapsed, true));
        titleRow.addView(headerToggle, new LinearLayout.LayoutParams(dp(38), dp(42)));
        newConvBtn = smallBtn("＋");
        newConvBtn.setContentDescription("新建会话");
        newConvBtn.setOnClickListener(v -> newConv());
        titleRow.addView(newConvBtn, new LinearLayout.LayoutParams(dp(44), dp(44)));
        header.addView(titleRow, new LinearLayout.LayoutParams(-1, -2));
        modelRow = new LinearLayout(this);
        modelRow.setOrientation(LinearLayout.HORIZONTAL);
        modelRow.setGravity(Gravity.CENTER_VERTICAL);
        modelSel = new Spinner(this);
        modelSel.setOnTouchListener((v, event) -> {
            if (event.getAction() == android.view.MotionEvent.ACTION_DOWN) {
                modelGestureStart = currentModel(); modelGesture = true;
                mainHandler.postDelayed(() -> modelGesture = false, 2000);
            }
            return false;
        });
        LinearLayout.LayoutParams mp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        modelRow.addView(modelSel, mp);
        reasoningSel = new Spinner(this);
        reasoningSel.setOnTouchListener((v, event) -> {
            if (event.getAction() == android.view.MotionEvent.ACTION_DOWN) {
                reasoningGesture = true; reasoningGestureModel = currentModel();
                mainHandler.postDelayed(() -> reasoningGesture = false, 2000);
            }
            return false;
        });
        modelRow.addView(reasoningSel, new LinearLayout.LayoutParams(dp(136), -2));
        reasoningSel.setVisibility(View.GONE);
        reasoningSel.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            public void onItemSelected(android.widget.AdapterView<?> p, View v, int pos, long id) {
                if (!reasoningGesture) return;
                String selectedModel = currentModel();
                if (!selectedModel.equals(reasoningGestureModel)) { reasoningGesture = false; return; }
                Object selected = p.getItemAtPosition(pos);
                if (selected != null) persistReasoningEffort(String.valueOf(selected));
                reasoningGesture = false;
            }
            public void onNothingSelected(android.widget.AdapterView<?> p) {}
        });
        filterBtn = smallBtn("👤");
        filterBtn.setContentDescription("只看用户消息");
        filterBtn.setOnClickListener(v -> toggleUserOnly());
        modelRow.addView(filterBtn, new LinearLayout.LayoutParams(dp(48), dp(42)));
        header.addView(modelRow, new LinearLayout.LayoutParams(-1, -2));
        chatgptPlanHint = mkText("Using ChatGPT plan · Manage usage", 11, Color.parseColor("#b9a2ff"));
        chatgptPlanHint.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        chatgptPlanHint.setPadding(dp(8), 0, dp(8), dp(3));
        chatgptPlanHint.setOnClickListener(v -> {
            try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://chatgpt.com/settings/usage"))); }
            catch (Exception e) { toast("无法打开 ChatGPT 使用情况页面"); }
        });
        header.addView(chatgptPlanHint, new LinearLayout.LayoutParams(-1, -2));
        chatgptPlanHint.setVisibility(View.GONE);
        main.addView(header);

        // 休眠唤醒横幅
        sleepBanner = new LinearLayout(this);
        sleepBanner.setOrientation(LinearLayout.HORIZONTAL);
        sleepBanner.setBackgroundColor(Color.parseColor("#4a3b10"));
        sleepBanner.setPadding(dp(12), dp(8), dp(12), dp(8));
        sleepBanner.setGravity(Gravity.CENTER_VERTICAL);
        sbText = new TextView(this);
        sbText.setText("⚠ 服务已被系统休眠（Termux 在后台被冻结）");
        sbText.setTextSize(12); sbText.setTextColor(Color.parseColor("#fbbf24"));
        sleepBanner.addView(sbText, new LinearLayout.LayoutParams(0, -2, 1f));
        Button wakeBtn = new Button(this);
        wakeBtn.setText("一键唤醒"); wakeBtn.setTextSize(12);
        wakeBtn.setTextColor(Color.WHITE);
        wakeBtn.setBackground(roundRect(Color.parseColor("#b45309"), dp(8)));
        wakeBtn.setOnClickListener(v -> wakeService());
        sleepBanner.addView(wakeBtn, new LinearLayout.LayoutParams(dp(80), dp(36)));
        sleepBanner.setVisibility(View.GONE);
        main.addView(sleepBanner, new LinearLayout.LayoutParams(-1, -2));

        // 消息区
        scroller = new ScrollView(this);
        scroller.setBackgroundColor(BG);
        msgList = new LinearLayout(this);
        msgList.setOrientation(LinearLayout.VERTICAL);
        msgList.setPadding(dp(10), dp(12), dp(10), dp(8));
        scroller.addView(msgList, new ViewGroup.LayoutParams(-1, -2));
        main.addView(scroller, new LinearLayout.LayoutParams(-1, 0, 1f));
        scroller.setOnScrollChangeListener((View v, int x, int y, int ox, int oy) -> {
            if (suppressScroll) return;
            int bottom = msgList.getHeight() - v.getHeight();
            stick = y >= bottom - dp(48);
            if (jumpBtn != null) jumpBtn.setVisibility(stick ? View.GONE : View.VISIBLE);
        });

        // 输入区
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setBackgroundColor(PANEL);
        bar.setGravity(Gravity.BOTTOM);
        bar.setPadding(dp(8), dp(8), dp(8), dp(8));
        input = new EditText(this);
        input.setHint("给 agent 下达指令…");
        input.setHintTextColor(DIM);
        input.setTextColor(TEXT);
        input.setBackgroundColor(CARD);
        input.setPadding(dp(12), dp(8), dp(12), dp(8));
        input.setMaxLines(4); input.setTextSize(15);
        input.setTextColor(TEXT);
        input.setImeOptions(EditorInfo.IME_FLAG_NO_ENTER_ACTION);
        input.setBackground(roundRect(CARD, dp(12)));
        input.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            public void onTextChanged(CharSequence s, int a, int b, int c) {}
            public void afterTextChanged(Editable s) {
                int lines = input.getLineCount();
                LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) input.getLayoutParams();
                lp.height = Math.min(dp(26 * Math.max(1, lines)) + dp(14), dp(140));
                input.setLayoutParams(lp);
                saveDraft();
            }
        });
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        bar.addView(input, ip);
        pickBtn = new Button(this);
        pickBtn.setText("🖼"); pickBtn.setTextSize(16);
        pickBtn.setBackground(roundRect(CARD, dp(12)));
        pickBtn.setOnClickListener(v -> pickImage());
        bar.addView(pickBtn, new LinearLayout.LayoutParams(dp(46), dp(48)));
        sendBtn = new Button(this);
        sendBtn.setText("➤"); sendBtn.setTextSize(16);
        sendBtn.setTextColor(Color.WHITE);
        sendBtn.setBackground(roundRect(ACCENT, dp(12)));
        sendBtn.setOnClickListener(v -> sendMessage());
        bar.addView(sendBtn, new LinearLayout.LayoutParams(dp(52), dp(48)));
        stopBtn = new Button(this);
        stopBtn.setText("■ 停止"); stopBtn.setTextSize(13);
        stopBtn.setTextColor(Color.WHITE);
        stopBtn.setBackground(roundRect(Color.parseColor("#5b2330"), dp(12)));
        stopBtn.setVisibility(View.GONE);
        stopBtn.setOnClickListener(v -> stopStream());
        bar.addView(stopBtn, new LinearLayout.LayoutParams(dp(70), dp(48)));
        main.addView(bar);
        root.addView(main, new FrameLayout.LayoutParams(-1, -1));
        setHeaderCollapsed(getPreferences(MODE_PRIVATE).getBoolean("header_collapsed", false), false);

        // 悬浮"一键到底"按钮（不占顶栏空间，避免挤掉 ＋）
        jumpBtn = smallBtn("▼");
        jumpBtn.setContentDescription("一键到底");
        jumpBtn.setVisibility(View.GONE);
        jumpBtn.setOnClickListener(v -> { stick = true; jumpBtn.setVisibility(View.GONE); scrollBottomForce(); });
        FrameLayout.LayoutParams jp = new FrameLayout.LayoutParams(dp(44), dp(44),
                Gravity.END | Gravity.BOTTOM);
        jp.setMargins(0, 0, dp(14), dp(92));
        root.addView(jumpBtn, jp);
        setContentView(root);
    }

    Button smallBtn(String t) {
        Button b = new Button(this);
        b.setText(t); b.setTextSize(14);
        b.setTextColor(TEXT);
        b.setMinimumWidth(0); b.setMinimumHeight(0);
        b.setPadding(dp(8), 0, dp(8), 0);
        b.setBackground(roundRect(CARD, dp(10)));
        b.setMinWidth(dp(38)); b.setMinHeight(dp(40));
        return b;
    }

    GradientDrawable roundRect(int color, float r) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color); d.setCornerRadius(r);
        d.setStroke(1, BORDER);
        return d;
    }

    int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    void toast(String s) {
        runOnUiThread(() -> android.widget.Toast.makeText(this, s, android.widget.Toast.LENGTH_SHORT).show());
    }

    // ---------------- 网络 ----------------

    interface HttpCb { void done(int code, String body); }

    void http(String method, String path, String jsonBody, HttpCb cb) {
        Thread t = new Thread(() -> {
            Exception lastErr = null;
            for (int attempt = 0; attempt < 2; attempt++) {
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(BASE + path).openConnection();
                    c.setRequestMethod(method);
                    c.setConnectTimeout(8000); c.setReadTimeout(15000);
                    c.setRequestProperty("Connection", "close");
                    if (jsonBody != null) {
                        c.setDoOutput(true);
                        c.setRequestProperty("Content-Type", "application/json");
                        OutputStream os = c.getOutputStream();
                        os.write(jsonBody.getBytes(StandardCharsets.UTF_8));
                        os.close();
                    }
                    int code = c.getResponseCode();
                    InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
                    StringBuilder sb = new StringBuilder();
                    if (is != null) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                        String line; while ((line = r.readLine()) != null) sb.append(line);
                        r.close();
                    }
                    c.disconnect();
                    final int fc = code; final String fb = sb.toString();
                    runOnUiThread(() -> cb.done(fc, fb));
                    return;
                } catch (Exception e) {
                    lastErr = e;
                    android.util.Log.w("PhoneAgent", "HTTP " + method + " " + path +
                            " attempt " + attempt + " failed", e);
                    try { Thread.sleep(600); } catch (InterruptedException ie) {}
                }
            }
            final String msg = lastErr == null || lastErr.getMessage() == null
                    ? "网络错误" : lastErr.getClass().getSimpleName() + ": " + lastErr.getMessage();
            runOnUiThread(() -> cb.done(-1, msg));
        });
        t.start();
    }

    void refreshStatus() {
        httpStatus((code, body) -> {
            if (code == 200) {
                final boolean wasWaking = waking;
                waking = false;
                failCount = 0;
                runOnUiThread(() -> {
                    sleepBanner.setVisibility(View.GONE);
                    sbText.setText("⚠ 服务已被系统休眠（Termux 在后台被冻结）");
                });
                statusDot.setText("● 在线"); statusDot.setTextColor(GREEN);
                if (wasWaking) toast("✅ 服务已恢复");
                try {
                    JSONObject o = new JSONObject(body);
                    statusSnapshot = o;
                    modelRegistry = o.optJSONObject("model_registry");
                    if (modelRegistry == null) modelRegistry = new JSONObject();
                    models.clear();
                    JSONArray arr = o.optJSONArray("models");
                    if (arr != null) for (int i = 0; i < arr.length(); i++) models.add(arr.optString(i));
                    visionModels.clear();
                    JSONArray vision = o.optJSONArray("vision_models");
                    if (vision != null) for (int i = 0; i < vision.length(); i++) visionModels.add(vision.optString(i));
                    modelLabels.clear();
                    JSONObject labels = o.optJSONObject("model_labels");
                    for (String m : models) modelLabels.add(labels == null ? m : labels.optString(m, m));
                    if (modelSel.getAdapter() == null || !sameModelsInAdapter()) buildModelSelector(o);
                    updateReasoningSelector(currentReasoningEffort);
                    updateImageAvailability();
                } catch (Exception ignored) {}
            } else {
                statusDot.setText("● 离线（服务未运行）"); statusDot.setTextColor(RED);
                failCount++;
                android.util.Log.i("PhoneAgent", "status fail fc=" + failCount +
                        " waking=" + waking + " lastHeal=" + lastHeal);
                if (failCount >= 2) {
                    long now = System.currentTimeMillis();
                    if (waking && now - wakeStart > 20000) {
                        waking = false;
                        runOnUiThread(() -> sbText.setText(
                            "⚠ 唤醒未生效：请检查 Termux 设置是否开启「允许外部应用」，并到系统设置把 Termux 省电策略设为「无限制」"));
                    } else if (!waking && now - lastHeal > 60000) {
                        healNow();
                    }
                    runOnUiThread(() -> sleepBanner.setVisibility(View.VISIBLE));
                }
            }
        });
    }

    boolean sameModelsInAdapter() {
        List<String> available = modelsForMode(currentMode);
        if (modelSel == null || modelSel.getAdapter() == null || modelSel.getAdapter().getCount() != available.size()) return false;
        StringBuilder sig = new StringBuilder();
        sig.append(currentMode).append('|');
        for (String model : available) sig.append(model).append('=').append(labelForModel(model)).append(';');
        return modelSignature.equals(sig.toString());
    }

    // 状态检查专用：短超时，Termux 冻结时不会每次卡 30 秒
    void httpStatus(HttpCb cb) {
        Thread t = new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(BASE + "/api/status").openConnection();
                c.setRequestMethod("GET");
                c.setConnectTimeout(3000); c.setReadTimeout(4000);
                c.setRequestProperty("Connection", "close");
                int code = c.getResponseCode();
                InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
                StringBuilder sb = new StringBuilder();
                if (is != null) {
                    BufferedReader r = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                    String line; while ((line = r.readLine()) != null) sb.append(line);
                    r.close();
                }
                c.disconnect();
                final int fc = code; final String fb = sb.toString();
                runOnUiThread(() -> cb.done(fc, fb));
            } catch (Exception e) {
                runOnUiThread(() -> cb.done(-1, "timeout"));
            }
        });
        t.start();
    }

    void wakeService() {
        if (!isTermuxInstalled()) { showSetupGuide("未检测到 Termux。请先安装 Termux，然后返回这里继续。"); return; }
        if (waking) return;
        waking = true;
        wakeStart = System.currentTimeMillis();
        sleepBanner.setVisibility(View.VISIBLE);
        sbText.setText("⚠ 正在唤醒 Termux…（4 秒后自动返回）");
        android.util.Log.i("PhoneAgent", "wake: heal#1 before opening termux");
        healNow();
        try {
            Intent i = new Intent();
            i.setClassName("com.termux", "com.termux.app.TermuxActivity");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            toast("打开 Termux 失败: " + e.getMessage());
        }
        // 打开 Termux 2 秒后再补发一次（等 MIUI 解冻，冻结时 RUN_COMMAND 无法送达）
        new android.os.Handler().postDelayed(() -> {
            android.util.Log.i("PhoneAgent", "wake: heal#2 after termux opened");
            healNow();
        }, 2000);
        // 4 秒后自动切回本应用（moveTaskToFront 不受 MIUI「后台弹出界面」限制；
        // 用反射调用，兼容精简版 android.jar 编译）
        new android.os.Handler().postDelayed(() -> {
            try {
                Activity.class.getMethod("moveTaskToFront", boolean.class)
                        .invoke(this, true);
                android.util.Log.i("PhoneAgent", "wake: auto-return OK");
            } catch (Exception e) {
                try {
                    Intent back = new Intent(this, MainActivity.class);
                    back.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                    startActivity(back);
                    android.util.Log.i("PhoneAgent", "wake: auto-return fallback OK");
                } catch (Exception e2) {
                    android.util.Log.w("PhoneAgent", "auto-return failed", e2);
                }
            }
        }, 4000);
    }

    // 通过 Termux RUN_COMMAND 服务：python 没在跑就重启，并持有 wake-lock
    void healNow() {
        try {
            Intent r = new Intent("com.termux.RUN_COMMAND");
            r.setClassName("com.termux", "com.termux.app.RunCommandService");
            String script = "if ! /data/data/com.termux/files/usr/bin/python3 -c \"import socket; socket.create_connection(('127.0.0.1',8756),1).close()\" 2>/dev/null; then cd /data/data/com.termux/files/home/.phoneagent && nohup /data/data/com.termux/files/usr/bin/python3 agent.py >> service.log 2>&1 & fi; termux-wake-lock 2>/dev/null || true";
            r.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
            r.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script});
            r.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
            r.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            r.putExtra("com.termux.RUN_COMMAND_SESSION_NEW", true);
            startService(r);
            lastHeal = System.currentTimeMillis();
            android.util.Log.i("PhoneAgent", "RUN_COMMAND startService OK");
        } catch (Exception e) {
            android.util.Log.w("PhoneAgent", "RUN_COMMAND failed", e);
        }
    }

    boolean isTermuxInstalled() {
        try { getPackageManager().getPackageInfo("com.termux", 0); return true; }
        catch (Exception e) { return false; }
    }

    void maybeOfferSetup() {
        if (!getPreferences(MODE_PRIVATE).getBoolean("setup_guide_seen", false)) {
            mainHandler.postDelayed(() -> showSetupGuide(null), 500);
        }
    }

    void showSetupGuide(String message) {
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(18), dp(8), dp(18), dp(8));
        TextView intro = mkText("PhoneAgent 需要 Termux 在本机运行服务。初始化只会安装应用文件，不会包含 API 密钥或会话数据。", 13, TEXT);
        box.addView(intro);
        TextView install = mkText("1. 安装 Termux 官方版本（F-Droid 或 GitHub 发布页），打开一次并完成首次设置。\n2. 首次运行时，在系统设置 → 应用 → PhoneAgent → 权限 → 其他权限中允许“在 Termux 环境中运行命令”。\n3. 点下方按钮复制初始化命令，在 Termux 粘贴运行；它会开启 Termux 的外部应用命令开关并安装基础包。\n4. 返回这里点“安装 / 修复服务”。\n5. Android 设置中将 Termux 电池用量设为“不受限制”。", 12, DIM);
        install.setPadding(0, dp(10), 0, dp(8)); box.addView(install);
        TextView state = mkText(message == null ? (isTermuxInstalled() ? "已检测到 Termux。" : "未检测到 Termux，请先安装后再继续。") : message, 12, message == null ? DIM : Color.parseColor("#fbbf24"));
        box.addView(state);
        if (!isTermuxInstalled()) {
            Button download = smallBtn("打开 Termux 官方下载页");
            download.setOnClickListener(v -> {
                try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/termux/termux-app/releases"))); }
                catch (Exception e) { toast("无法打开下载页面"); }
            });
            LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(-1, dp(42)); dl.topMargin = dp(6); box.addView(download, dl);
        }
        Button setup = smallBtn("安装 / 修复服务"); setup.setEnabled(isTermuxInstalled());
        setup.setOnClickListener(v -> { startBackendSetup(state); });
        LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(-1, dp(44)); sl.topMargin = dp(8); box.addView(setup, sl);
        Button copy = smallBtn("复制 Termux 初始化命令");
        copy.setOnClickListener(v -> {
            String cmd = "P=\"$HOME/.termux/termux.properties\"; mkdir -p \"$HOME/.termux\"; touch \"$P\"; if grep -q '^[[:space:]]*allow-external-apps[[:space:]]*=' \"$P\"; then sed -i 's/^[[:space:]]*allow-external-apps[[:space:]]*=.*/allow-external-apps = true/' \"$P\"; else printf '\\n%s\\n' 'allow-external-apps = true' >> \"$P\"; fi; if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then pkg install -y python curl; fi; python3 -c 'import cryptography' 2>/dev/null || pkg install -y python-cryptography; python3 -c 'import jwt' 2>/dev/null || python3 -m pip install PyJWT";
            ((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("PhoneAgent Termux", cmd));
            toast("命令已复制。请在 Termux 粘贴运行；该命令只安装基础运行环境。");
        });
        LinearLayout.LayoutParams cl = new LinearLayout.LayoutParams(-1, dp(44)); cl.topMargin = dp(6); box.addView(copy, cl);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("首次设置 PhoneAgent").setView(box)
                .setNegativeButton("稍后", (d,w) -> getPreferences(MODE_PRIVATE).edit().putBoolean("setup_guide_seen", true).apply())
                .setPositiveButton("完成", (d,w) -> getPreferences(MODE_PRIVATE).edit().putBoolean("setup_guide_seen", true).apply()).create(); dialog.show();
    }

    void startBackendSetup(TextView state) {
        if (!isTermuxInstalled()) { state.setText("未检测到 Termux，请先安装 Termux。"); return; }
        state.setText("正在准备安全的一次性本机下载…");
        Thread setup = new Thread(() -> {
            ServerSocket server = null;
            boolean serverTransferred = false;
            try {
                String token = java.util.UUID.randomUUID().toString().replace("-", "");
                String assetHash = sha256Asset("backend.zip");
                server = new ServerSocket(18757, 1, java.net.InetAddress.getByName("127.0.0.1"));
                server.setSoTimeout(30 * 60 * 1000);
                final ServerSocket oneShot = server;
                Thread serverThread = new Thread(() -> serveBackendZip(oneShot, token), "PhoneAgent-bootstrap");
                serverThread.start();
                String script = "set -e; exec >>\"$HOME/.phoneagent-setup.log\" 2>&1; " +
                        "echo 'PhoneAgent install started'; if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then pkg install -y python curl; fi; " +
                        "python3 -c 'import cryptography' 2>/dev/null || pkg install -y python-cryptography; " +
                        "python3 -c 'import jwt' 2>/dev/null || python3 -m pip install PyJWT; " +
                        "T=$(mktemp \"$HOME/.phoneagent-package.XXXXXX\"); D=$(mktemp -d \"$HOME/.phoneagent-install.XXXXXX\"); trap 'rm -f \"$T\"; rm -rf \"$D\"' EXIT; " +
                        "curl -fsS --max-time 110 http://127.0.0.1:18757/bootstrap/" + token + " -o \"$T\"; " +
                        "python3 -c 'import hashlib,sys; assert hashlib.sha256(open(sys.argv[1],\"rb\").read()).hexdigest()==sys.argv[2]' \"$T\" " + assetHash + "; " +
                        "python3 -c 'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' \"$T\" \"$D\"; " +
                        "bash \"$D/share_install.sh\"; termux-wake-lock 2>/dev/null || true; echo 'PhoneAgent setup command completed'";
                Intent r = new Intent("com.termux.RUN_COMMAND");
                r.setClassName("com.termux", "com.termux.app.RunCommandService");
                r.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
                r.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script});
                r.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
                r.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
                r.putExtra("com.termux.RUN_COMMAND_SESSION_NEW", true);
                startService(r);
                serverTransferred = true;
                lastHeal = System.currentTimeMillis();
                runOnUiThread(() -> state.setText("已发送初始化命令。可在 Termux 运行 tail -f ~/.phoneagent-setup.log 查看进度；完成后会自动连接。"));
                mainHandler.postDelayed(() -> { refreshStatus(); }, 12000);
            } catch (Exception e) {
                runOnUiThread(() -> state.setText("初始化未能启动：请检查 Android 的 PhoneAgent 权限中是否允许“在 Termux 环境中运行命令”，并确认 Termux 外部应用开关已开启，然后重试。"));
            } finally { if (!serverTransferred) try { if (server != null) server.close(); } catch (Exception ignored) {} }
        }, "PhoneAgent-setup");
        setup.start();
    }

    String sha256Asset(String name) throws Exception {
        InputStream in = getAssets().open(name); java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
        byte[] buf = new byte[8192]; int n; while ((n = in.read(buf)) != -1) digest.update(buf, 0, n); in.close();
        StringBuilder hex = new StringBuilder(); for (byte b : digest.digest()) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 0xff));
        return hex.toString();
    }

    void serveBackendZip(ServerSocket server, String token) {
        long deadline = System.currentTimeMillis() + 30L * 60L * 1000L; int requests = 0;
        try {
            while (requests++ < 32 && System.currentTimeMillis() < deadline) {
                Socket client = null;
                try {
                    client = server.accept(); client.setSoTimeout(3000);
                    InputStream input = client.getInputStream();
                    String request = readBoundedHttpLine(input, 2048); int headerBytes = request == null ? 0 : request.length() + 2; String header;
                    while ((header = readBoundedHttpLine(input, 2048)) != null && !header.isEmpty()) {
                        headerBytes += header.length() + 2; if (headerBytes > 8192) throw new java.io.IOException("HTTP headers too large");
                    }
                    OutputStream out = client.getOutputStream();
                    if (("GET /bootstrap/" + token + " HTTP/1.1").equals(request)) {
                        InputStream in = getAssets().open("backend.zip");
                        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buf = new byte[8192]; int n;
                        while ((n = in.read(buf)) != -1) bytes.write(buf, 0, n); in.close();
                        byte[] payload = bytes.toByteArray();
                        out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: " + payload.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                        out.write(payload); out.flush();
                        return;
                    }
                    out.write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".getBytes(StandardCharsets.US_ASCII)); out.flush();
                } catch (java.net.SocketTimeoutException ignored) {
                } catch (Exception e) { android.util.Log.w("PhoneAgent", "bootstrap request failed", e); }
                finally { if (client != null) try { client.close(); } catch (Exception ignored) {} }
            }
        } catch (Exception e) { android.util.Log.w("PhoneAgent", "bootstrap server failed", e); }
        finally { try { server.close(); } catch (Exception ignored) {} }
    }

    String readBoundedHttpLine(InputStream input, int maxBytes) throws Exception {
        StringBuilder line = new StringBuilder(); int ch, bytes = 0;
        while ((ch = input.read()) != -1) {
            if (++bytes > maxBytes) throw new java.io.IOException("HTTP line too long");
            if (ch == '\n') return line.toString();
            if (ch != '\r') line.append((char) ch);
        }
        return line.length() == 0 ? null : line.toString();
    }

    void buildModelSelector(JSONObject st) {
        final android.content.SharedPreferences sp = getPreferences(MODE_PRIVATE);
        String def = st.optString("chat".equals(currentMode) ? "chat_default_model" : "default_model", models.isEmpty() ? "" : models.get(0));
        String saved = sp.getString("model_" + currentMode, "work".equals(currentMode) ? sp.getString("model", def) : def);
        final String selectorMode = currentMode;
        final List<String> available = modelsForMode(selectorMode);
        int sel = available.indexOf(saved);
        if (sel < 0) sel = available.indexOf(def);
        if (sel < 0) sel = 0;
        List<String> names = new ArrayList<String>();
        for (String model : available) names.add(labelForModel(model));
        StringBuilder sig = new StringBuilder();
        sig.append(selectorMode).append('|');
        for (int i = 0; i < available.size(); i++) sig.append(available.get(i)).append('=').append(names.get(i)).append(';');
        modelSignature = sig.toString();
        restoringModel = true;
        final int restoreGeneration = ++modelRestoreGeneration;
        modelSel.setAdapter(new BaseAdapter() {
            public int getCount() { return names.size(); }
            public Object getItem(int p) { return names.get(p); }
            public long getItemId(int p) { return p; }
            public View getView(int p, View cv, ViewGroup pg) {
                TextView tv = new TextView(MainActivity.this);
                tv.setText(names.get(p)); tv.setTextColor(TEXT); tv.setTextSize(12);
                tv.setPadding(dp(8), dp(6), dp(8), dp(6));
                return tv;
            }
        });
        modelSel.setSelection(sel);
        mainHandler.postDelayed(() -> { if (restoreGeneration == modelRestoreGeneration) restoringModel = false; }, 500);
        modelSel.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            public void onItemSelected(android.widget.AdapterView<?> p, View v, int pos, long id) {
                if (pos >= 0 && pos < available.size()) {
                    String model = available.get(pos);
                    boolean explicitChoice = modelGesture && !model.equals(modelGestureStart);
                    if (explicitChoice && !restoringModel && selectorMode.equals(currentMode)) {
                        android.content.SharedPreferences.Editor edit = sp.edit().putString("model_" + selectorMode, model);
                        if ("work".equals(selectorMode)) edit.putString("model", model);
                        edit.apply();
                        if (curConv != null) persistSettings("model", model);
                        updateReasoningSelector(currentReasoningEffort);
                    }
                    if (modelGesture) modelGesture = false;
                }
                updateImageAvailability();
            }
            public void onNothingSelected(android.widget.AdapterView<?> p) {}
        });
    }

    List<String> modelsForMode(String mode) {
        ArrayList<String> out = new ArrayList<String>();
        for (String model : models) {
            JSONObject meta = modelRegistry.optJSONObject(model);
            JSONArray modes = meta == null ? null : meta.optJSONArray("modes");
            if (modes == null || modes.length() == 0 || contains(modes, mode)) out.add(model);
        }
        return out;
    }

    boolean contains(JSONArray arr, String value) {
        if (arr == null) return false;
        for (int i = 0; i < arr.length(); i++) if (value.equals(arr.optString(i))) return true;
        return false;
    }

    String labelForModel(String model) {
        JSONObject labels = statusSnapshot.optJSONObject("model_labels");
        return labels == null ? model : labels.optString(model, model);
    }

    void updateImageAvailability() {
        if (chatgptPlanHint != null) chatgptPlanHint.setVisibility(!headerCollapsed && currentModel().startsWith("chatgpt/") ? View.VISIBLE : View.GONE);
        if (pickBtn == null) return;
        boolean supported = visionModels.contains(currentModel());
        pickBtn.setVisibility(supported ? View.VISIBLE : View.GONE);
        if (!supported && pendingImg != null) {
            pendingImg = null;
            toast("当前模型不支持图片，已移除附件");
        }
    }

    void setHeaderCollapsed(boolean collapsed, boolean save) {
        headerCollapsed = collapsed;
        if (modelRow != null) modelRow.setVisibility(collapsed ? View.GONE : View.VISIBLE);
        if (chatgptPlanHint != null) chatgptPlanHint.setVisibility(!collapsed && currentModel().startsWith("chatgpt/") ? View.VISIBLE : View.GONE);
        if (newConvBtn != null) newConvBtn.setVisibility(collapsed ? View.GONE : View.VISIBLE);
        if (headerToggle != null) {
            headerToggle.setText(collapsed ? "⌄" : "⌃");
            headerToggle.setContentDescription(collapsed ? "展开顶部工具栏" : "收起顶部工具栏");
        }
        if (save) getPreferences(MODE_PRIVATE).edit().putBoolean("header_collapsed", collapsed).apply();
    }

    List<String> reasoningEfforts(String model) {
        ArrayList<String> result = new ArrayList<String>();
        JSONObject meta = modelRegistry.optJSONObject(model);
        JSONArray values = meta == null ? null : meta.optJSONArray("reasoning_efforts");
        if (values == null) return result;
        String[] supported = {"low", "medium", "high", "xhigh", "max"};
        for (int i = 0; i < values.length(); i++) {
            String effort = values.optString(i, "");
            boolean allowed = false;
            for (String candidate : supported) if (candidate.equals(effort)) { allowed = true; break; }
            if (allowed && !result.contains(effort)) result.add(effort);
        }
        return result;
    }

    String effortLabel(String effort) {
        if ("low".equals(effort)) return "低 · low";
        if ("medium".equals(effort)) return "中 · medium";
        if ("high".equals(effort)) return "高 · high";
        if ("xhigh".equals(effort)) return "很高 · xhigh";
        if ("max".equals(effort)) return "最大 · max";
        return effort;
    }

    String defaultReasoningEffort(String model, List<String> choices) {
        JSONObject meta = modelRegistry.optJSONObject(model);
        String value = meta == null ? "" : meta.optString("default_reasoning_effort", "");
        if (choices.contains(value)) return value;
        if (choices.contains("low")) return "low";
        return choices.isEmpty() ? "" : choices.get(0);
    }

    void updateReasoningSelector(String preferred) {
        if (reasoningSel == null) return;
        final String model = currentModel();
        final List<String> choices = reasoningEfforts(model);
        if (choices.isEmpty()) {
            reasoningSel.setVisibility(View.GONE); currentReasoningEffort = ""; return;
        }
        String selected = choices.contains(preferred) ? preferred : defaultReasoningEffort(model, choices);
        ArrayList<String> labels = new ArrayList<String>();
        for (String value : choices) labels.add(effortLabel(value));
        restoringReasoning = true;
        final int generation = ++reasoningRestoreGeneration;
        reasoningSel.setAdapter(new BaseAdapter() {
            public int getCount() { return labels.size(); }
            public Object getItem(int p) { return choices.get(p); }
            public long getItemId(int p) { return p; }
            public View getView(int p, View cv, ViewGroup parent) {
                TextView tv = new TextView(MainActivity.this); tv.setText(labels.get(p));
                tv.setTextColor(TEXT); tv.setTextSize(11); tv.setPadding(dp(4), dp(6), dp(4), dp(6)); return tv;
            }
        });
        reasoningSel.setVisibility(View.VISIBLE);
        reasoningSel.setSelection(Math.max(0, choices.indexOf(selected)));
        currentReasoningEffort = selected;
        mainHandler.postDelayed(() -> { if (generation == reasoningRestoreGeneration) restoringReasoning = false; }, 500);
    }

    void persistReasoningEffort(String effort) {
        String id = curConv, model = currentModel(), old = currentReasoningEffort;
        if (id == null || !reasoningEfforts(model).contains(effort)) { updateReasoningSelector(old); return; }
        currentReasoningEffort = effort;
        JSONObject body = new JSONObject();
        try { body.put("reasoning_effort", effort); }
        catch (Exception e) { currentReasoningEffort = old; updateReasoningSelector(old); return; }
        http("POST", "/api/convs/" + id + "/settings", body.toString(), (code, response) -> {
            if (code < 200 || code >= 300) {
                if (id.equals(curConv) && model.equals(currentModel())) {
                    currentReasoningEffort = old; updateReasoningSelector(old);
                }
                toast("保存思考强度失败: " + response);
            }
        });
    }

    String modeLabel(String mode) { return "chat".equals(mode) ? "Chat" : "Work"; }

    String createConvBody() {
        JSONObject o = new JSONObject();
        try { o.put("mode", currentMode); } catch (Exception ignored) {}
        return o.toString();
    }

    void setCurrentMode(String mode, boolean rebuildModels) {
        modelGesture = false;
        currentMode = "chat".equals(mode) ? "chat" : "work";
        if (modeBtn != null) {
            modeBtn.setText(modeLabel(currentMode) + " ▾");
            modeBtn.setBackground(roundRect(Color.parseColor("chat".equals(currentMode) ? "#3c294a" : "#1e3245"), dp(12)));
            modeBtn.setTextColor(Color.parseColor("chat".equals(currentMode) ? "#efccff" : "#c7e0ff"));
        }
        if (input != null) input.setHint("chat".equals(currentMode) ? "说说你在想什么…" : "给助手下达任务…");
        if (rebuildModels && statusSnapshot != null) buildModelSelector(statusSnapshot);
        if (reasoningSel != null) updateReasoningSelector(currentReasoningEffort);
    }

    void setModeSwitching(boolean switching) {
        modeSwitching = switching;
        boolean enabled = !switching && !busy && !creatingConversation && !preparingSend;
        if (modeBtn != null) modeBtn.setEnabled(enabled);
        if (menuBtn != null) menuBtn.setEnabled(enabled);
        if (newConvBtn != null) newConvBtn.setEnabled(enabled);
        if (modelSel != null) modelSel.setEnabled(enabled);
        if (filterBtn != null) filterBtn.setEnabled(enabled);
        if (sendBtn != null) sendBtn.setEnabled(enabled);
        if (input != null) input.setEnabled(!switching && !creatingConversation);
        if (pickBtn != null) pickBtn.setEnabled(enabled);
        if (reasoningSel != null) reasoningSel.setEnabled(enabled);
    }

    boolean isCurrentModeSwitch(int token, String target) {
        return modeSwitching && token == modeSwitchGeneration && target != null && target.equals(modeSwitchTarget);
    }

    void finishModeSwitch(int token) {
        if (token != modeSwitchGeneration) return;
        modeSwitchTarget = null; modeSwitchPrevious = null; modeSwitchPreviousConv = null;
        setModeSwitching(false);
    }

    void failModeSwitch(int token, String message) {
        if (token != modeSwitchGeneration) return;
        String previousMode = modeSwitchPrevious == null ? "work" : modeSwitchPrevious;
        String previousConv = modeSwitchPreviousConv;
        String previousDraft = modeSwitchPreviousDraft;
        setCurrentMode(previousMode, true);
        if (previousConv != null && !previousConv.isEmpty() && !previousConv.equals(curConv)) {
            // Keep the input/navigation lock until the prior conversation is restored.
            loadConv(previousConv, true, token, true);
        } else if (previousConv == null || previousConv.isEmpty()) {
            curConv = null; ++viewGeneration; pendingImg = null;
            msgList.removeAllViews(); convTitle.setText("PhoneAgent");
            input.setText(previousDraft == null ? "" : previousDraft);
            finishModeSwitch(token);
        } else {
            finishModeSwitch(token);
        }
        if (message != null && !message.isEmpty()) toast(message);
    }

    void chooseMode() {
        if (busy || creatingConversation || preparingSend || modeSwitching) { toast("当前操作期间不能切换模式"); return; }
        final String[] options = {"Work · 使用项目目录与工作技能", "Chat · 日常对话"};
        new AlertDialog.Builder(this).setTitle("选择模式")
                .setSingleChoiceItems(options, "chat".equals(currentMode) ? 1 : 0, (d, which) -> {
                    d.dismiss(); switchMode(which == 1 ? "chat" : "work");
                }).setNegativeButton("取消", null).show();
    }

    void switchMode(final String mode) {
        if (mode.equals(currentMode)) return;
        if (busy || creatingConversation || preparingSend || modeSwitching) { toast("当前操作期间不能切换模式"); return; }
        final int token = ++modeSwitchGeneration;
        modeSwitchTarget = mode;
        modeSwitchPrevious = currentMode;
        modeSwitchPreviousConv = curConv;
        modeSwitchPreviousDraft = input == null ? "" : input.getText().toString();
        setModeSwitching(true);
        saveDraft();
        http("GET", "/api/convs", null, (code, body) -> {
            if (!isCurrentModeSwitch(token, mode)) return;
            if (busy || preparingSend) { failModeSwitch(token, "任务已开始，模式切换已取消"); return; }
            if (code != 200) { failModeSwitch(token, "无法读取会话列表，已恢复原模式"); return; }
            ArrayList<ConvInfo> items = new ArrayList<ConvInfo>();
            try {
                JSONArray arr = new JSONObject(body).optJSONArray("convs");
                if (arr == null) { failModeSwitch(token, "会话列表响应无法识别，已恢复原模式"); return; }
                if (arr != null) for (int i = 0; i < arr.length(); i++) {
                    JSONObject o = arr.optJSONObject(i); if (o == null) continue;
                    ConvInfo c = new ConvInfo(); c.id = o.optString("id", "");
                    c.title = o.optString("title", "新对话"); c.mode = o.optString("mode", "");
                    c.cnt = o.optInt("cnt", 0); c.updated = o.optLong("updated", 0);
                    if (!c.id.isEmpty()) items.add(c);
                }
            } catch (Exception ignored) { failModeSwitch(token, "会话列表响应无法识别，已恢复原模式"); return; }
            findModeConversation(items, mode, 0, token);
        });
    }

    void findModeConversation(final List<ConvInfo> items, final String mode, final int index, final int token) {
        if (!isCurrentModeSwitch(token, mode)) return;
        if (busy || preparingSend) { failModeSwitch(token, "任务已开始，模式切换已取消"); return; }
        if (index >= items.size()) { createConversationForMode(mode, token); return; }
        ConvInfo c = items.get(index);
        if (mode.equals(c.mode)) { setCurrentMode(mode, true); loadConv(c.id, true, token, false); return; }
        http("GET", "/api/convs/" + c.id + "/messages", null, (code, body) -> {
            if (!isCurrentModeSwitch(token, mode)) return;
            if (busy || preparingSend) { failModeSwitch(token, "任务已开始，模式切换已取消"); return; }
            if (code != 200 && code != 404) { failModeSwitch(token, "查找目标会话失败，已恢复原模式"); return; }
            if (code == 404) { findModeConversation(items, mode, index + 1, token); return; }
            JSONObject session = code == 200 ? safeObj(body).optJSONObject("session") : null;
            String found = session == null ? c.mode : session.optString("mode", c.mode);
            if (found.isEmpty()) found = "work";
            if (mode.equals(found)) { setCurrentMode(mode, true); loadConv(c.id, true, token, false); }
            else findModeConversation(items, mode, index + 1, token);
        });
    }

    void createConversationForMode(String mode, int token) {
        if (!isCurrentModeSwitch(token, mode)) return;
        if (busy || preparingSend) { failModeSwitch(token, "任务已开始，模式切换已取消"); return; }
        final String targetMode = mode;
        http("POST", "/api/convs", "{\"mode\":\"" + targetMode + "\"}", (code, body) -> {
            if (!isCurrentModeSwitch(token, targetMode)) return;
            if (busy || preparingSend) { failModeSwitch(token, "任务已开始，模式切换已取消"); return; }
            if (code != 200 && code != 201) { failModeSwitch(token, "创建 " + modeLabel(targetMode) + " 会话失败，已恢复原模式"); return; }
            String id = safeObj(body).optString("id", "");
            if (id.isEmpty()) { failModeSwitch(token, "创建会话失败：响应无效，已恢复原模式"); return; }
            setCurrentMode(targetMode, true);
            curConv = id; ++viewGeneration; pendingImg = null;
            currentReasoningEffort = ""; updateReasoningSelector("");
            failedConv = null; failedModel = null; lastFailureBubble = null;
            msgList.removeAllViews(); convTitle.setText("新对话 · " + modeLabel(targetMode));
            restoreDraft(id); persistSettings("model", currentModel()); refreshConvs(false);
            finishModeSwitch(token);
        });
    }

    void applySessionSettings(JSONObject settings) {
        currentWorkdir = settings.optString("workdir", "");
        currentMemoryEnabled = settings.optBoolean("memory_enabled", true);
        currentIdentity = settings.optString("identity", "");
        currentScene = settings.optString("scene", "");
        currentPerspective = settings.optString("perspective", "");
        currentReasoningEffort = settings.optString("reasoning_effort", "");
        String model = settings.optString("model", "");
        List<String> available = modelsForMode(currentMode);
        int pos = available.indexOf(model);
        if (pos >= 0 && modelSel != null) {
            restoringModel = true;
            final int restoreGeneration = ++modelRestoreGeneration;
            modelSel.setSelection(pos);
            mainHandler.postDelayed(() -> { if (restoreGeneration == modelRestoreGeneration) restoringModel = false; }, 500);
        }
        updateReasoningSelector(currentReasoningEffort);
    }

    void persistSettings(String key, Object value) {
        if (curConv == null) return;
        JSONObject body = new JSONObject();
        try { body.put(key, value); } catch (Exception ignored) { return; }
        http("POST", "/api/convs/" + curConv + "/settings", body.toString(), (code, response) -> {
            if (code < 200 || code >= 300) toast("保存设置失败: " + response);
        });
    }

    void showCurrentActions() {
        if (curConv == null) { toast("当前还没有会话"); return; }
        new AlertDialog.Builder(this).setTitle("当前会话")
                .setItems(new String[]{"改名", "导出"}, (d, which) -> {
                    if (which == 0) renameCurrentConv(); else exportCurrentConv();
                }).show();
    }

    void openAdvancedWeb() {
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(BASE + "/web/"))); }
        catch (Exception e) { toast("无法打开完整网页版: " + safeMessage(e)); }
    }

    void showPresets() {
        if (!"chat".equals(currentMode)) { toast("角色预设只用于 Chat 模式"); return; }
        if (curConv == null) { toast("请先打开一个 Chat 会话"); return; }
        JSONArray presets = statusSnapshot.optJSONArray("presets");
        if (presets == null || presets.length() == 0) { toast("服务端没有提供角色预设"); return; }
        String[] labels = new String[presets.length()]; ArrayList<JSONObject> values = new ArrayList<JSONObject>();
        for (int i = 0; i < presets.length(); i++) {
            JSONObject preset = presets.optJSONObject(i); if (preset == null) preset = new JSONObject();
            labels[i] = preset.optString("name", "角色 " + (i + 1)); values.add(preset);
        }
        new AlertDialog.Builder(this).setTitle("Chat 角色预设").setItems(labels, (d, which) -> {
            final String conv = curConv;
            JSONObject req = new JSONObject(); try { req.put("identity", values.get(which).optString("identity", "")); } catch (Exception ignored) {}
            http("POST", "/api/convs/" + conv + "/settings", req.toString(), (code, body) -> {
                if (code >= 200 && code < 300) {
                    if (conv.equals(curConv)) {
                        JSONObject session = safeObj(body).optJSONObject("session");
                        JSONObject settings = session == null ? null : session.optJSONObject("settings");
                        if (settings != null) applySessionSettings(settings);
                    }
                    toast("角色预设已应用");
                } else toast("应用失败: " + body);
            });
        }).setNegativeButton("取消", null).show();
    }

    void showSessionSettings() {
        if (curConv == null) { toast("请先打开一个会话"); return; }
        final String settingsConv = curConv;
        final String settingsMode = currentMode;
        LinearLayout form = new LinearLayout(this); form.setPadding(dp(18), dp(8), dp(18), dp(4));
        form.setOrientation(LinearLayout.VERTICAL);
        final boolean isWork = "work".equals(settingsMode);
        final EditText workdir = isWork ? settingsInput("工作目录（绝对路径）", currentWorkdir) : null;
        final EditText identity = isWork ? null : settingsInput("身份 / 指令", currentIdentity);
        final EditText scene = isWork ? null : settingsInput("场景", currentScene);
        final EditText perspective = isWork ? null : settingsInput("当前角色名 / 视角", currentPerspective);
        final android.widget.CheckBox memory = isWork ? null : new android.widget.CheckBox(this);
        if ("work".equals(settingsMode)) {
            TextView note = mkText("请输入 Termux 中的绝对项目路径，例如 /data/data/com.termux/files/home/project。", 12, DIM);
            form.addView(note);
            workdir.setSingleLine(true); form.addView(workdir);
        } else {
            form.addView(identity); form.addView(scene); form.addView(perspective);
            memory.setText("启用长期记忆");
            memory.setTextColor(TEXT); memory.setChecked(currentMemoryEnabled); form.addView(memory);
        }
        ScrollView scroll = new ScrollView(this); scroll.addView(form);
        new AlertDialog.Builder(this).setTitle("会话设置 · " + modeLabel(currentMode)).setView(scroll)
                .setPositiveButton("保存", (d, w) -> {
                    JSONObject body = new JSONObject();
                    if ("work".equals(settingsMode)) {
                        String path = workdir.getText().toString().trim();
                        if (!path.isEmpty() && (!path.startsWith("/") || path.contains("content://"))) {
                            toast("请输入 Termux 可访问的绝对路径"); return;
                        }
                        if (!path.isEmpty()) try { body.put("workdir", path); } catch (Exception ignored) {}
                    } else {
                        try {
                            body.put("identity", identity.getText().toString().trim());
                            body.put("scene", scene.getText().toString().trim());
                            body.put("perspective", perspective.getText().toString().trim());
                            body.put("memory_enabled", memory.isChecked());
                        } catch (Exception ignored) {}
                    }
                    if (body.length() == 0) { toast("没有需要保存的设置"); return; }
                    http("POST", "/api/convs/" + settingsConv + "/settings", body.toString(), (c, b) -> {
                        if (c < 200 || c >= 300) { toast("保存失败: " + b); return; }
                        if (settingsConv.equals(curConv)) {
                            JSONObject session = safeObj(b).optJSONObject("session");
                            JSONObject settings = session == null ? null : session.optJSONObject("settings");
                            if (settings != null) applySessionSettings(settings);
                        }
                        toast("设置已保存");
                    });
                }).setNegativeButton("取消", null).show();
    }

    EditText settingsInput(String hint, String value) {
        EditText e = new EditText(this); e.setHint(hint); e.setText(value); e.setMinLines(1); e.setMaxLines(3);
        e.setTextColor(TEXT); return e;
    }

    void showSkills() {
        if (curConv == null) { toast("请先打开一个会话"); return; }
        http("GET", "/api/convs/" + curConv + "/skills", null, (code, body) -> {
            if (code != 200) { toast("读取技能失败: " + body); return; }
            JSONArray skills = safeObj(body).optJSONArray("skills");
            if (skills == null) { showInfo("技能", formatJson(body)); return; }
            String[] names = new String[skills.length()]; boolean[] enabled = new boolean[skills.length()];
            for (int i = 0; i < skills.length(); i++) {
                JSONObject s = skills.optJSONObject(i); if (s == null) s = new JSONObject();
                String name = s.optString("name", s.optString("id", "技能"));
                names[i] = name + (s.optString("description", "").isEmpty() ? "" : "\n" + s.optString("description"));
                enabled[i] = s.optBoolean("enabled", !s.optBoolean("disabled", false));
            }
            new AlertDialog.Builder(this).setTitle("技能 · 勾选启用").setMultiChoiceItems(names, enabled, (d, i, checked) -> enabled[i] = checked)
                    .setPositiveButton("保存", (d, w) -> {
                        JSONArray disabled = new JSONArray();
                        for (int i = 0; i < skills.length(); i++) if (!enabled[i]) {
                            JSONObject s = skills.optJSONObject(i); if (s != null) disabled.put(s.optString("id", s.optString("name", "")));
                        }
                        JSONObject req = new JSONObject(); try { req.put("disabled_skills", disabled); } catch (Exception ignored) {}
                        http("POST", "/api/convs/" + curConv + "/settings", req.toString(), (c, b) -> toast(c >= 200 && c < 300 ? "技能设置已保存" : "保存失败: " + b));
                    }).setNegativeButton("关闭", null).show();
        });
    }

    void showMemories() {
        if (curConv == null) { toast("请先打开一个会话"); return; }
        http("GET", "/api/convs/" + curConv + "/memories", null, (code, body) -> {
            if (code != 200) { toast("读取记忆失败: " + body); return; }
            JSONArray arr = safeObj(body).optJSONArray("memories");
            if (arr == null || arr.length() == 0) {
                new AlertDialog.Builder(this).setTitle("长期记忆").setMessage("暂无记忆。")
                        .setPositiveButton("新增", (d, w) -> editMemory(null)).setNegativeButton("关闭", null).show(); return;
            }
            String[] items = new String[arr.length() + 1]; items[0] = "＋ 新增记忆";
            ArrayList<JSONObject> data = new ArrayList<JSONObject>();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject m = arr.optJSONObject(i); if (m == null) m = new JSONObject(); data.add(m);
                items[i + 1] = m.optString("content", "（空记忆）") + "\n" + m.optString("kind", "") + " · " + m.optString("epistemic", "") + " · " + m.optString("validity", "active");
            }
            new AlertDialog.Builder(this).setTitle("长期记忆 · 点击管理").setItems(items, (d, which) -> {
                if (which == 0) editMemory(null); else memoryActions(data.get(which - 1));
            }).setNegativeButton("关闭", null).show();
        });
    }

    void memoryActions(JSONObject memory) {
        String validity = memory.optString("validity", "active");
        String[] actions = "retracted".equals(validity) ? new String[]{"修订内容", "恢复为有效"} : new String[]{"修订内容", "撤回记忆"};
        new AlertDialog.Builder(this).setTitle("记忆操作").setItems(actions, (d, which) -> {
            if (which == 0) editMemory(memory);
            else updateMemoryValidity(memory, "retracted".equals(validity) ? "active" : "retracted");
        }).show();
    }

    void editMemory(JSONObject prior) {
        LinearLayout form = new LinearLayout(this); form.setOrientation(LinearLayout.VERTICAL); form.setPadding(dp(18), dp(8), dp(18), 0);
        EditText content = settingsInput("记忆内容", prior == null ? "" : prior.optString("content", "")); form.addView(content);
        TextView kindLabel = mkText("类别", 12, DIM); form.addView(kindLabel);
        final String[] kinds = {"fact", "claim", "belief", "preference", "promise", "event", "plan", "state"};
        android.widget.Spinner kind = new android.widget.Spinner(this);
        android.widget.ArrayAdapter<String> kindAdapter = new android.widget.ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, kinds);
        kindAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item); kind.setAdapter(kindAdapter);
        int kindPos = 1; if (prior != null) for (int i = 0; i < kinds.length; i++) if (kinds[i].equals(prior.optString("kind", "claim"))) kindPos = i;
        kind.setSelection(kindPos); form.addView(kind);
        TextView epLabel = mkText("认知状态", 12, DIM); form.addView(epLabel);
        final String[] epistemics = {"confirmed", "claimed", "believed", "unverified"};
        android.widget.Spinner epistemic = new android.widget.Spinner(this);
        android.widget.ArrayAdapter<String> epAdapter = new android.widget.ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, epistemics);
        epAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item); epistemic.setAdapter(epAdapter);
        int epPos = 1; if (prior != null) for (int i = 0; i < epistemics.length; i++) if (epistemics[i].equals(prior.optString("epistemic", "claimed"))) epPos = i;
        epistemic.setSelection(epPos); form.addView(epistemic);
        EditText entity = settingsInput("关联实体（纠错时保留）", prior == null ? "user" : prior.optString("entity", "user")); form.addView(entity);
        EditText audience = settingsInput("可见对象（如 all 或角色名）", prior == null ? "all" : prior.optString("audience", "all")); form.addView(audience);
        EditText slot = settingsInput("信息槽位（可选）", prior == null ? "" : prior.optString("slot", "")); form.addView(slot);
        String oldSource = "";
        if (prior != null && !prior.isNull("source_id")) oldSource = prior.optString("source_id", "");
        EditText sourceId = settingsInput("来源消息编号（可选数字）", oldSource); form.addView(sourceId);
        new AlertDialog.Builder(this).setTitle(prior == null ? "新增记忆" : "创建修订版")
                .setView(form).setPositiveButton("保存", (d, w) -> {
                    String text = content.getText().toString().trim(); if (text.isEmpty()) { toast("记忆内容不能为空"); return; }
                    String sourceText = sourceId.getText().toString().trim();
                    Long sourceNumber = null;
                    if (!sourceText.isEmpty()) {
                        try { sourceNumber = Long.parseLong(sourceText); if (sourceNumber <= 0) throw new NumberFormatException(); }
                        catch (NumberFormatException e) { toast("来源编号必须是正整数，或留空"); return; }
                    }
                    JSONObject req = new JSONObject();
                    try {
                        req.put("content", text); req.put("kind", kinds[kind.getSelectedItemPosition()]);
                        req.put("epistemic", epistemics[epistemic.getSelectedItemPosition()]);
                        req.put("entity", entity.getText().toString().trim());
                        req.put("audience", audience.getText().toString().trim());
                        req.put("slot", slot.getText().toString().trim());
                        if (sourceNumber != null) req.put("source_id", sourceNumber.longValue());
                        if (prior != null) req.put("revises", prior.optString("id", ""));
                    } catch (Exception ignored) {}
                    http("POST", "/api/convs/" + curConv + "/memories", req.toString(), (c, b) -> toast(c >= 200 && c < 300 ? "记忆已保存" : "保存失败: " + b));
                }).setNegativeButton("取消", null).show();
    }

    void updateMemoryValidity(JSONObject memory, String validity) {
        JSONObject req = new JSONObject(); try { req.put("id", memory.optString("id", "")); req.put("validity", validity); } catch (Exception ignored) {}
        http("POST", "/api/convs/" + curConv + "/memory-edit", req.toString(), (c, b) -> toast(c >= 200 && c < 300 ? ("active".equals(validity) ? "记忆已恢复" : "记忆已撤回") : "操作失败: " + b));
    }

    void showApprovals() {
        if (!"work".equals(currentMode)) { showInfo("工具权限", "Chat 模式不使用工具。"); return; }
        if (curConv == null) { toast("请先打开一个 Work 会话"); return; }

        LinearLayout panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(16), dp(8), dp(16), dp(8));
        TextView scope = mkText("Work 自动批准（所有工作会话）", 15, TEXT); scope.setTypeface(Typeface.DEFAULT_BOLD);
        panel.addView(scope, new LinearLayout.LayoutParams(-1, -2));
        Switch autoSwitch = new Switch(this); autoSwitch.setText("自动批准 Work 工具操作"); autoSwitch.setTextColor(TEXT);
        LinearLayout.LayoutParams switchLp = new LinearLayout.LayoutParams(-1, -2); switchLp.topMargin = dp(6); panel.addView(autoSwitch, switchLp);
        Switch outsideSwitch = new Switch(this); outsideSwitch.setText("允许访问工作区外文件（Termux 权限范围）"); outsideSwitch.setTextColor(TEXT);
        LinearLayout.LayoutParams outsideLp = new LinearLayout.LayoutParams(-1, -2); outsideLp.topMargin = dp(2); panel.addView(outsideSwitch, outsideLp);
        TextView warning = mkText("开启后，命令、删除、覆盖和导出会直接执行。命令使用 Termux 已有权限；工作目录不是命令沙箱。文件读取和修改都遵循 Termux 当前权限；受保护的私有凭据不会作为模型文件内容。Chat 模式没有工具。", 12, DIM);
        warning.setPadding(0, dp(4), 0, dp(12)); panel.addView(warning);
        TextView pendingTitle = mkText("待批准操作", 14, TEXT); pendingTitle.setTypeface(Typeface.DEFAULT_BOLD);
        panel.addView(pendingTitle);
        ScrollView scroll = new ScrollView(this); LinearLayout pendingList = new LinearLayout(this); pendingList.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(pendingList, new ScrollView.LayoutParams(-1, -2));
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(-1, dp(230)); scrollLp.topMargin = dp(4); panel.addView(scroll, scrollLp);
        final boolean[] policyLoaded = {false}; final boolean[] policySaving = {false}; final boolean[] policyValue = {false};
        final boolean[] outsideLoaded = {false}; final boolean[] outsideSaving = {false}; final boolean[] outsideValue = {false};
        autoSwitch.setEnabled(false); outsideSwitch.setEnabled(false);
        final AlertDialog dialog = new AlertDialog.Builder(this).setTitle("工具权限").setView(panel)
                .setPositiveButton("关闭", null).create();
        dialog.show();
        http("GET", "/api/approval-policy", null, (code, response) -> {
            if (!dialog.isShowing()) return;
            if (code != 200 || !safeObj(response).has("auto_approve_work") || !safeObj(response).has("allow_outside_workspace")) {
                toast("读取 Work 工具权限失败: " + response); return;
            }
            policyValue[0] = safeObj(response).optBoolean("auto_approve_work", false); policyLoaded[0] = true;
            autoSwitch.setOnCheckedChangeListener(null); autoSwitch.setChecked(policyValue[0]); autoSwitch.setEnabled(true);
            outsideValue[0] = safeObj(response).optBoolean("allow_outside_workspace", false); outsideLoaded[0] = true;
            outsideSwitch.setOnCheckedChangeListener(null); outsideSwitch.setChecked(outsideValue[0]); outsideSwitch.setEnabled(true);
            warning.setText((outsideValue[0] ? "文件工具可访问 Termux 权限内路径。" : "文件工具限制在当前项目范围内。") + "开启后，命令、删除、覆盖和导出会直接执行。命令使用 Termux 已有权限；工作目录不是命令沙箱。文件读取和修改都遵循 Termux 当前权限；受保护的私有凭据不会作为模型文件内容。切回项目范围可随时关闭此项。Chat 模式没有工具。");
            autoSwitch.setOnCheckedChangeListener((b, checked) -> {
                if (!policyLoaded[0] || policySaving[0]) return;
                policySaving[0] = true; autoSwitch.setEnabled(false);
                postApprovalPolicy(checked, "auto_approve_work", autoSwitch, policyLoaded, policySaving, policyValue, "Work 自动批准已");
            });
            outsideSwitch.setOnCheckedChangeListener((b, checked) -> {
                if (!outsideLoaded[0] || outsideSaving[0]) return;
                outsideSaving[0] = true; outsideSwitch.setEnabled(false);
                postApprovalPolicy(checked, "allow_outside_workspace", outsideSwitch, outsideLoaded, outsideSaving, outsideValue, "工作区外访问已");
            });
        });
        final String requestConv = curConv;
        http("GET", "/api/convs/" + requestConv + "/approvals", null, (code, response) -> {
            if (!dialog.isShowing() || !requestConv.equals(curConv)) return;
            if (code != 200) { pendingList.addView(mkText("读取待批准操作失败：" + response, 12, RED)); return; }
            JSONArray arr = safeObj(response).optJSONArray("approvals");
            if (arr == null || arr.length() == 0) { pendingList.addView(mkText("当前没有待处理的批准请求。", 12, DIM)); return; }
            for (int i = 0; i < arr.length(); i++) {
                JSONObject approval = arr.optJSONObject(i); if (approval == null) continue;
                TextView row = mkText(approval.optString("tool", "操作") + "\n" + approval.optString("args", "") + "\n" + approval.optString("output", approval.optString("description", "")), 12, TEXT);
                row.setTextIsSelectable(true); row.setPadding(dp(10), dp(8), dp(10), dp(8)); row.setBackground(roundRect(CARD, dp(9)));
                LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(-1, -2); rowLp.topMargin = dp(5); pendingList.addView(row, rowLp);
                row.setEnabled(!busy); row.setAlpha(busy ? 0.5f : 1f);
                row.setOnClickListener(v -> decideApproval(approval));
            }
        });
    }

    void postApprovalPolicy(boolean value, String field, Switch target, boolean[] loaded, boolean[] saving, boolean[] previous, String toastPrefix) {
        JSONObject req = new JSONObject(); try { req.put(field, value); } catch (Exception ignored) {}
        http("POST", "/api/approval-policy", req.toString(), (code, response) -> {
            boolean ok = code >= 200 && code < 300;
            if (ok) {
                JSONObject result = safeObj(response); previous[0] = result.has(field) ? result.optBoolean(field, value) : value;
                toast(toastPrefix + (previous[0] ? "开启" : "关闭"));
            } else {
                target.setOnCheckedChangeListener(null); target.setChecked(previous[0]);
                toast("保存工具权限失败: " + response);
            }
            saving[0] = false; target.setEnabled(loaded[0]);
            target.setOnCheckedChangeListener((b, checked) -> {
                if (!loaded[0] || saving[0]) return;
                saving[0] = true; target.setEnabled(false);
                postApprovalPolicy(checked, field, target, loaded, saving, previous, toastPrefix);
            });
        });
    }

    void decideApproval(JSONObject approval) {
        if (busy) { toast("请等当前任务停止后再批准"); return; }
        String id = approval.optString("approval_id", approval.optString("id", ""));
        if (id.isEmpty()) { showInfo("批准请求", formatJson(approval.toString())); return; }
        String detail = "工具：" + approval.optString("tool", "未知") + "\n参数：\n" + approval.optString("args", "") + "\n\n" + approval.optString("output", "");
        new AlertDialog.Builder(this).setTitle("批准操作？").setMessage(detail)
                .setPositiveButton("批准", (d, w) -> postApproval(id, true))
                .setNegativeButton("拒绝", (d, w) -> postApproval(id, false)).setNeutralButton("取消", null).show();
    }

    void postApproval(String id, boolean approve) {
        if (busy) { toast("请等当前任务停止后再批准"); return; }
        JSONObject req = new JSONObject(); try { req.put("id", id); req.put("approve", approve); } catch (Exception ignored) {}
        final String conv = curConv;
        http("POST", "/api/convs/" + conv + "/approve", req.toString(), (c, b) -> {
            boolean changed = c >= 200 && c < 300 && safeObj(b).optBoolean("ok", false);
            if (changed) toast(approve ? "已批准，请点击“重试本轮”继续执行" : "已拒绝");
            else { toast("操作未生效，批准请求可能已过期或会话仍在运行: " + b); if (conv.equals(curConv)) setApprovalActionsEnabled(msgList, !busy); }
        });
    }

    void showQuota() {
        http("GET", "/api/quota", null, (code, body) -> {
            if (code != 200) { toast("读取配额失败: " + body); return; }
            JSONObject root = safeObj(body); JSONArray quotas = root.optJSONArray("quota");
            StringBuilder text = new StringBuilder("本机记录的估算，不代表共享服务的真实剩余额度。重置时区：America/Los_Angeles。\n");
            if (quotas != null) for (int i = 0; i < quotas.length(); i++) {
                JSONObject q = quotas.optJSONObject(i); if (q == null) continue;
                JSONObject limits = q.optJSONObject("limits"); if (limits == null) limits = new JSONObject();
                String model = q.optString("model", "未知模型");
                text.append("\n").append(labelForModel(model)).append("\n")
                        .append("今日请求：").append(q.optInt("requests_today", 0))
                        .append(" · 输入 ").append(q.optLong("input_tokens", 0))
                        .append(" / 输出 ").append(q.optLong("output_tokens", 0)).append(" tokens\n")
                        .append("用量未知：").append(q.optInt("unknown_usage_requests", 0)).append(" 次\n")
                        .append("本地估算剩余：").append(q.isNull("estimated_remaining") ? "不限" : q.optString("estimated_remaining", "不限"))
                        .append(" · RPM ").append(limitLabel(limits, "rpm"))
                        .append(" · TPM ").append(limitLabel(limits, "tpm"))
                        .append(" · RPD ").append(limitLabel(limits, "rpd"));
            }
            if (quotas == null) text.append("\n配额响应格式无法识别：\n").append(formatJson(body));
            if (contextBudget > 0) text.append("\n\n当前上下文估算：").append(contextEstimate).append(" / ").append(contextBudget).append(" tokens");
            showInfo("配额与上下文", text.toString());
        });
    }

    String limitLabel(JSONObject limits, String key) {
        return limits == null || limits.isNull(key) || limits.optString(key, "").isEmpty() || "0".equals(limits.optString(key, "")) ? "未设置" : limits.optString(key);
    }

    void showContextSettings() {
        if (!"work".equals(currentMode)) { toast("上下文窗口设置仅用于 Work 模式"); return; }
        if (curConv == null) { toast("请先打开一个 Work 会话"); return; }
        final String conversation = curConv;
        http("GET", "/api/convs/" + conversation + "/context", null, (code, body) -> {
            if (code != 200 || !conversation.equals(curConv)) { toast("读取上下文设置失败: " + body); return; }
            JSONObject context = safeObj(body);
            LinearLayout form = new LinearLayout(this); form.setOrientation(LinearLayout.VERTICAL);
            form.setPadding(dp(18), dp(8), dp(18), dp(4));
            Switch enabled = new Switch(this); enabled.setText("自动压缩旧上下文（关闭时仍按预算裁切原文）"); enabled.setTextColor(TEXT);
            enabled.setChecked(context.optBoolean("enabled", true)); form.addView(enabled);
            EditText budget = settingsInput("上下文预算（2048–64000 tokens）", String.valueOf(context.optInt("budget", 12000)));
            budget.setInputType(android.text.InputType.TYPE_CLASS_NUMBER); form.addView(budget);
            int estimate = context.optInt("estimated_input_tokens", contextEstimate);
            TextView estimateView = mkText("", 12, DIM);
            estimateView.setPadding(0, dp(6), 0, dp(6)); form.addView(estimateView);
            Runnable updateEstimate = () -> estimateView.setText(enabled.isChecked()
                    ? "当前估算：" + estimate + " tokens。超出预算时自动压缩旧上下文。"
                    : "当前估算：" + estimate + " tokens。自动压缩已关闭，超出预算时仍会裁切原文。");
            enabled.setOnCheckedChangeListener((button, checked) -> updateEstimate.run()); updateEstimate.run();
            JSONObject compaction = context.optJSONObject("compaction");
            JSONArray checkpoints = compaction == null ? null : compaction.optJSONArray("checkpoints");
            TextView checkpointTitle = mkText("最近压缩检查点", 13, TEXT); checkpointTitle.setTypeface(Typeface.DEFAULT_BOLD); form.addView(checkpointTitle);
            if (checkpoints == null || checkpoints.length() == 0) {
                form.addView(mkText("暂无检查点。", 12, DIM));
            } else {
                int start = Math.max(0, checkpoints.length() - 3);
                for (int i = checkpoints.length() - 1; i >= start; i--) {
                    JSONObject item = checkpoints.optJSONObject(i); if (item == null) continue;
                    String summary = item.optString("summary", "");
                    if (summary.isEmpty()) continue;
                    TextView checkpoint = mkText(item.optString("created_at", "") + " · 模型归纳（供参考，不是事实核验）\n" + summary, 12, DIM);
                    checkpoint.setTextIsSelectable(true); checkpoint.setPadding(dp(9), dp(7), dp(9), dp(7));
                    checkpoint.setBackground(roundRect(CARD, dp(8))); form.addView(checkpoint);
                }
            }
            new AlertDialog.Builder(this).setTitle("Work 上下文").setView(form)
                    .setPositiveButton("保存", (dialog, which) -> {
                        int tokenBudget;
                        try { tokenBudget = Integer.parseInt(budget.getText().toString().trim()); }
                        catch (Exception e) { toast("预算请输入正整数"); return; }
                        if (tokenBudget < 2048 || tokenBudget > 64000) { toast("预算范围为 2048–64000 tokens"); return; }
                        JSONObject request = new JSONObject();
                        try { request.put("enabled", enabled.isChecked()); request.put("budget", tokenBudget); } catch (Exception ignored) {}
                        http("POST", "/api/convs/" + conversation + "/context", request.toString(), (saveCode, response) -> {
                            if (saveCode < 200 || saveCode >= 300) { toast("保存上下文设置失败: " + response); return; }
                            JSONObject saved = safeObj(response);
                            contextBudget = saved.optInt("budget", tokenBudget);
                            contextEstimate = saved.optInt("estimated_input_tokens", contextEstimate);
                            JSONObject session = saved.optJSONObject("session");
                            JSONObject sessionSettings = session == null ? null : session.optJSONObject("settings");
                            if (sessionSettings != null && conversation.equals(curConv)) applySessionSettings(sessionSettings);
                            toast("Work 上下文设置已保存");
                        });
                    }).setNegativeButton("关闭", null).show();
        });
    }

    void showProviderSettings() {
        http("GET", "/api/provider-settings", null, (code, body) -> {
            if (code != 200) { toast("读取 API 设置失败（HTTP " + code + "）"); return; }
            JSONObject state = safeObj(body);
            LinearLayout panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL);
            panel.setPadding(dp(16), dp(8), dp(16), dp(8));
            TextView note = mkText("密钥只保存在此手机的 PhoneAgent 配置中。已保存的密钥不会回显。留空表示保留现值；勾选清除后保存才会删除。也可以不填 API 密钥，直接使用 ChatGPT 账户登录。", 12, DIM);
            panel.addView(note);
            EditText deepseek = secretKeyInput("DeepSeek API Key");
            EditText gemini = secretKeyInput("Gemini API Key");
            EditText exa = secretKeyInput("Exa API Key（可选搜索）");
            CheckBox clearDeepseek = new CheckBox(this); clearDeepseek.setText("清除 DeepSeek 密钥");
            CheckBox clearGemini = new CheckBox(this); clearGemini.setText("清除 Gemini 密钥");
            CheckBox clearExa = new CheckBox(this); clearExa.setText("清除 Exa 密钥");
            String[] keys = {"deepseek_key_configured", "gemini_key_configured", "exa_key_configured"};
            boolean[] configured = new boolean[keys.length];
            for (int i = 0; i < keys.length; i++) configured[i] = state.optBoolean(keys[i], false);
            TextView status = mkText("密钥状态：DeepSeek " + (configured[0] ? "已设置" : "未设置") + " · Gemini " +
                    (configured[1] ? "已设置" : "未设置") + " · Exa " + (configured[2] ? "已设置" : "未设置"), 12, DIM);
            panel.addView(status);
            panel.addView(deepseek); panel.addView(clearDeepseek);
            panel.addView(gemini); panel.addView(clearGemini);
            panel.addView(exa); panel.addView(clearExa);
            ScrollView scroll = new ScrollView(this); scroll.addView(panel);
            AlertDialog dialog = new AlertDialog.Builder(this).setTitle("API 与连接").setView(scroll)
                    .setNegativeButton("取消", null).setPositiveButton("保存", null).create();
            dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                if (busy || preparingSend || modeSwitching) { toast("请先停止当前任务，再修改 API 设置。"); return; }
                JSONObject req = new JSONObject();
                try {
                    if (clearDeepseek.isChecked()) req.put("clear_deepseek_key", true);
                    else putNonBlank(req, "deepseek_key", deepseek.getText().toString());
                    if (clearGemini.isChecked()) req.put("clear_gemini_key", true);
                    else putNonBlank(req, "gemini_key", gemini.getText().toString());
                    if (clearExa.isChecked()) req.put("clear_exa_key", true);
                    else putNonBlank(req, "exa_key", exa.getText().toString());
                } catch (Exception ignored) {}
                http("POST", "/api/provider-settings", req.toString(), (saveCode, response) -> {
                    if (saveCode < 200 || saveCode >= 300) {
                        toast(saveCode == 409 ? "当前任务仍在运行，请先停止任务后再保存。" : "保存 API 设置失败（HTTP " + saveCode + "），请确认服务版本后重试。");
                        return;
                    }
                    dialog.dismiss(); refreshStatus(); toast("API 设置已保存；密钥状态已更新。");
                });
            }));
            dialog.show();
        });
    }

    void putNonBlank(JSONObject object, String key, String value) {
        if (value != null && !value.trim().isEmpty()) try { object.put(key, value.trim()); } catch (Exception ignored) {}
    }

    EditText secretKeyInput(String hint) {
        EditText field = new EditText(this); field.setSingleLine(true); field.setHint(hint);
        field.setHintTextColor(DIM); field.setTextColor(TEXT); field.setInputType(0x00000081);
        field.setBackground(roundRect(CARD, dp(9))); field.setPadding(dp(12), 0, dp(12), 0);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(44)); lp.topMargin = dp(8); field.setLayoutParams(lp);
        return field;
    }

    void showChatGptAccounts() {
        http("GET", "/api/chatgpt/accounts", null, (code, body) -> {
            if (code != 200) { toast("读取 ChatGPT 账户失败: " + body); return; }
            JSONObject data = safeObj(body); JSONArray accounts = data.optJSONArray("accounts");
            LinearLayout panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL);
            panel.setPadding(dp(16), dp(8), dp(16), dp(8));
            TextView intro = mkText("通过官方 OAuth 在系统浏览器授权。密码和令牌不会显示在 PhoneAgent。", 12, DIM);
            panel.addView(intro);
            String activeId = data.optString("active_id", "");
            JSONObject activeAccount = null;
            if (accounts != null) for (int i = 0; i < accounts.length(); i++) {
                JSONObject candidate = accounts.optJSONObject(i);
                if (candidate != null && candidate.optBoolean("plan_enabled", false)
                        && (candidate.optBoolean("active", candidate.optString("id", "").equals(activeId))
                        || candidate.optString("id", "").equals(activeId))) { activeAccount = candidate; break; }
            }
            if (activeAccount != null) {
                String welcomeKey = "chatgpt_plan_welcomed_" + activeAccount.optString("id", "");
                if (!getPreferences(MODE_PRIVATE).getBoolean(welcomeKey, false)) {
                    getPreferences(MODE_PRIVATE).edit().putBoolean(welcomeKey, true).apply();
                    TextView welcome = mkText("You're using your ChatGPT plan. ChatGPT models use this account's plan.", 13, TEXT);
                    welcome.setPadding(dp(9), dp(7), dp(9), dp(7)); welcome.setBackground(roundRect(CARD, dp(8))); panel.addView(welcome);
                    Button usage = smallBtn("Manage usage");
                    usage.setOnClickListener(v -> {
                        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://chatgpt.com/settings/usage"))); }
                        catch (Exception e) { toast("无法打开 ChatGPT 使用情况页面"); }
                    });
                    panel.addView(usage, new LinearLayout.LayoutParams(-1, dp(38)));
                }
            }
            Button login = smallBtn("Continue with ChatGPT"); login.setTextColor(Color.WHITE); login.setBackground(roundRect(ACCENT, dp(9)));
            login.setEnabled(data.optBoolean("available", true));
            login.setOnClickListener(v -> startChatGptLogin(null));
            LinearLayout.LayoutParams loginLp = new LinearLayout.LayoutParams(-1, dp(44)); loginLp.setMargins(0, dp(7), 0, dp(7)); panel.addView(login, loginLp);
            if (!data.optBoolean("available", true)) panel.addView(mkText(data.optString("reason", "本机暂不可用 ChatGPT OAuth。"), 12, DIM));
            ScrollView scroll = new ScrollView(this); LinearLayout rows = new LinearLayout(this); rows.setOrientation(LinearLayout.VERTICAL);
            scroll.addView(rows, new ScrollView.LayoutParams(-1, -2));
            if (accounts == null || accounts.length() == 0) rows.addView(mkText("尚无已授权账户。", 13, DIM));
            else for (int i = 0; i < accounts.length(); i++) {
                JSONObject account = accounts.optJSONObject(i); if (account == null) continue;
                final JSONObject rowAccount = account; final String id = account.optString("id", "");
                LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL); card.setPadding(dp(10), dp(8), dp(10), dp(8));
                card.setBackground(roundRect(CARD, dp(9)));
                String status = account.optBoolean("active", id.equals(data.optString("active_id"))) ? "当前账户" : "已保存账户";
                if (!account.optBoolean("plan_enabled", false)) status += " · 套餐未授权，请继续授权";
                TextView label = mkText(account.optString("label", "ChatGPT") + (account.optString("email", "").isEmpty() ? "" : " · " + account.optString("email", "")), 14, TEXT);
                TextView sub = mkText(status, 12, account.optBoolean("plan_enabled", false) ? DIM : Color.parseColor("#fbbf24"));
                card.addView(label); card.addView(sub);
                LinearLayout actions = new LinearLayout(this); actions.setOrientation(LinearLayout.HORIZONTAL);
                Button select = smallBtn("切换账户"); select.setEnabled(account.optBoolean("plan_enabled", false));
                select.setOnClickListener(v -> selectChatGptAccount(id));
                Button auth = smallBtn("继续授权"); auth.setEnabled(data.optBoolean("available", true)); auth.setOnClickListener(v -> startChatGptLogin(id));
                Button logout = smallBtn("退出"); logout.setOnClickListener(v -> logoutChatGptAccount(id));
                actions.addView(select, new LinearLayout.LayoutParams(0, dp(38), 1f));
                actions.addView(auth, new LinearLayout.LayoutParams(0, dp(38), 1f));
                actions.addView(logout, new LinearLayout.LayoutParams(0, dp(38), 0.75f));
                card.addView(actions);
                LinearLayout.LayoutParams cardLp = new LinearLayout.LayoutParams(-1, -2); cardLp.topMargin = dp(5); rows.addView(card, cardLp);
            }
            panel.addView(scroll, new LinearLayout.LayoutParams(-1, dp(250)));
            Button refresh = smallBtn("刷新 ChatGPT 模型"); refresh.setOnClickListener(v -> refreshChatGptModels());
            LinearLayout.LayoutParams refreshLp = new LinearLayout.LayoutParams(-1, dp(42)); refreshLp.topMargin = dp(7); panel.addView(refresh, refreshLp);
            new AlertDialog.Builder(this).setTitle("ChatGPT 账户").setView(panel).setPositiveButton("关闭", null).show();
        });
    }

    void startChatGptLogin(String accountId) {
        JSONObject request = new JSONObject();
        try { if (accountId != null && !accountId.isEmpty()) request.put("account_id", accountId); } catch (Exception ignored) {}
        http("POST", "/api/chatgpt/login", request.toString(), (code, body) -> {
            if (code < 200 || code >= 300) { toast("启动 ChatGPT 授权失败: " + body); return; }
            String url = safeObj(body).optString("url", "");
            try {
                Uri uri = Uri.parse(url); String host = uri.getHost();
                boolean official = "https".equalsIgnoreCase(uri.getScheme()) && host != null
                        && (host.equalsIgnoreCase("chatgpt.com") || host.endsWith(".chatgpt.com")
                        || host.equalsIgnoreCase("openai.com") || host.endsWith(".openai.com"));
                if (!official) { toast("服务器没有返回有效的 OpenAI 官方授权地址"); return; }
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (Exception e) { toast("无法打开系统浏览器: " + safeMessage(e)); }
        });
    }

    void selectChatGptAccount(String id) {
        JSONObject request = new JSONObject(); try { request.put("account_id", id); } catch (Exception ignored) {}
        http("POST", "/api/chatgpt/select", request.toString(), (code, body) -> {
            if (code < 200 || code >= 300) { toast("切换 ChatGPT 账户失败: " + body); return; }
            refreshChatGptModels();
        });
    }

    void logoutChatGptAccount(String id) {
        JSONObject request = new JSONObject(); try { request.put("account_id", id); } catch (Exception ignored) {}
        http("POST", "/api/chatgpt/logout", request.toString(), (code, body) -> {
            if (code < 200 || code >= 300) { toast("退出 ChatGPT 账户失败: " + body); return; }
            toast("ChatGPT 账户已退出"); showChatGptAccounts();
        });
    }

    void refreshChatGptModels() {
        http("POST", "/api/chatgpt/models", "{}", (code, body) -> {
            if (code < 200 || code >= 300) { toast("刷新 ChatGPT 模型失败: " + body); return; }
            refreshStatus(); toast("ChatGPT 模型已刷新"); showChatGptAccounts();
        });
    }

    String formatJson(String body) {
        try { return new JSONObject(body).toString(2); } catch (Exception e) { try { return new JSONArray(body).toString(2); } catch (Exception ignored) { return body; } }
    }

    void showInfo(String title, String text) {
        TextView view = mkText(text, 13, TEXT); view.setTextIsSelectable(true); view.setPadding(dp(18), dp(12), dp(18), dp(12));
        ScrollView scroll = new ScrollView(this); scroll.addView(view);
        new AlertDialog.Builder(this).setTitle(title).setView(scroll).setPositiveButton("关闭", null).show();
    }

    void refreshConvs(boolean openAfterPick) {
        http("GET", "/api/convs", null, (code, body) -> {
            if (code != 200) return;
            convs.clear();
            try {
                JSONArray arr = new JSONObject(body).getJSONArray("convs");
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject o = arr.getJSONObject(i);
                    ConvInfo ci = new ConvInfo();
                    ci.id = o.getString("id");
                    ci.title = o.optString("title", "新对话");
                    ci.mode = o.optString("mode", "");
                    ci.cnt = o.optInt("cnt", 0);
                    ci.updated = o.optLong("updated", 0);
                    convs.add(ci);
                }
            } catch (Exception ignored) {}
            if (curConv == null && !convs.isEmpty() && !creatingConversation && !busy && !modeSwitching
                    && input.getText().toString().trim().isEmpty() && pendingImg == null)
                for (ConvInfo ci : convs) if (ci.mode.equals(currentMode) || (ci.mode.isEmpty() && "work".equals(currentMode))) { loadConv(ci.id); break; }
            if (openAfterPick) {
                ArrayList<ConvInfo> visible = new ArrayList<ConvInfo>();
                for (ConvInfo ci : convs) if (ci.mode.equals(currentMode) || (ci.mode.isEmpty() && "work".equals(currentMode))) visible.add(ci);
                showConvsDialogWithList(visible);
            }
        });
    }

    void loadConv(String id) {
        loadConv(id, false, 0, false);
    }

    void loadConv(String id, boolean duringModeSwitch, int switchToken, boolean restoringPreviousMode) {
        if (busy || creatingConversation || preparingSend || (modeSwitching && !duringModeSwitch)) {
            if (duringModeSwitch && isCurrentModeSwitch(switchToken, modeSwitchTarget)) {
                if (!restoringPreviousMode) failModeSwitch(switchToken, "任务已开始，模式切换已取消");
                else finishModeSwitch(switchToken);
            } else toast("当前操作仍在进行，请稍后再切换会话");
            return;
        }
        if (duringModeSwitch && switchToken != modeSwitchGeneration) return;
        modelGesture = false;
        saveDraft();
        final long generation = ++viewGeneration;
        curConv = id;
        pendingImg = null;
        restoreDraft(id);
        failedConv = null; failedModel = null; lastFailureBubble = null;
        msgList.removeAllViews(); convTitle.setText(titleOf(id));
        http("GET", "/api/convs/" + id + "/messages", null, (code, body) -> {
            if (generation != viewGeneration || !id.equals(curConv)) {
                if (duringModeSwitch && isCurrentModeSwitch(switchToken, modeSwitchTarget))
                    failModeSwitch(switchToken, "会话状态已变化，模式切换已取消");
                return;
            }
            if (code != 200) {
                addInlineStatus("无法加载会话（" + code + "）");
                if (duringModeSwitch) {
                    if (restoringPreviousMode) finishModeSwitch(switchToken);
                    else failModeSwitch(switchToken, "目标会话加载失败，已恢复原模式");
                }
                return;
            }
            try {
                JSONObject payload = new JSONObject(body);
                JSONObject session = payload.optJSONObject("session");
                if (session != null) {
                    String loadedMode = session.optString("mode", currentMode);
                    if ("work".equals(loadedMode) || "chat".equals(loadedMode)) setCurrentMode(loadedMode, !loadedMode.equals(currentMode));
                    JSONObject settings = session.optJSONObject("settings");
                    if (settings != null) applySessionSettings(settings);
                }
                JSONArray arr = payload.getJSONArray("messages");
                JSONObject run = payload.optJSONObject("run");
                boolean hasMessageRetry = false;
                for (int i = 0; i < arr.length(); i++) {
                    if (generation != viewGeneration || !id.equals(curConv)) return;
                    JSONObject m = arr.getJSONObject(i);
                    String role = m.getString("role");
                    String content = m.optString("content", "");
                    JSONObject meta = safeObj(m.optString("meta", "{}"));
                    if ("user".equals(role)) {
                        if (meta != null && meta.optBoolean("injected", false)) {
                            LinearLayout chip = addUserMsg("📷 agent 查看了图片：" + (meta.optString("path", "")));
                            chip.setVisibility(View.GONE);
                        } else {
                            LinearLayout uw = addUserMsg(extractPartsText(content));
                            extractPartsImg(content, uw);
                        }
                    }
                    else if ("assistant".equals(role)) {
                        if (meta.optBoolean("retryable", false)) hasMessageRetry = true;
                        renderHistoryAssistant(content, meta);
                    }
                }
                if (run != null && run.optBoolean("retryable", false) && !hasMessageRetry)
                    renderPersistedRun(run);
                scrollBottom();
                if (duringModeSwitch) finishModeSwitch(switchToken);
            } catch (Exception e) {
                addInlineStatus("会话内容无法显示: " + safeMessage(e));
                if (duringModeSwitch) {
                    if (restoringPreviousMode) finishModeSwitch(switchToken);
                    else failModeSwitch(switchToken, "目标会话无法显示，已恢复原模式");
                }
            }
        });
    }

    String titleOf(String id) {
        for (ConvInfo c : convs) if (c.id.equals(id)) return c.title;
        return "PhoneAgent";
    }

    JSONObject safeObj(String s) {
        try { return new JSONObject(s == null || s.isEmpty() ? "{}" : s); }
        catch (Exception e) { return new JSONObject(); }
    }

    void newConv() {
        if (busy || creatingConversation || preparingSend || modeSwitching) { toast("当前操作仍在进行"); return; }
        saveDraft();
        creatingConversation = true;
        input.setEnabled(false); sendBtn.setEnabled(false); pickBtn.setEnabled(false);
        http("POST", "/api/convs", createConvBody(), (code, body) -> {
            creatingConversation = false;
            input.setEnabled(true); sendBtn.setEnabled(true); pickBtn.setEnabled(true);
            if (code == 201 || code == 200) {
                try { curConv = new JSONObject(body).getString("id"); } catch (Exception ignored) {}
                if (curConv == null) { toast("创建会话失败: 响应无效"); return; }
                ++viewGeneration;
                failedConv = null; failedModel = null; lastFailureBubble = null;
                pendingImg = null;
                currentReasoningEffort = ""; updateReasoningSelector("");
                msgList.removeAllViews(); convTitle.setText("新对话");
                restoreDraft(curConv);
                persistSettings("model", currentModel());
                refreshConvs(false);
            } else toast("创建失败: " + body);
        });
    }

    void deleteConv(final String id) {
        if (busy || creatingConversation || preparingSend || modeSwitching) { toast("当前操作仍在进行"); return; }
        new AlertDialog.Builder(this)
            .setTitle("删除会话")
            .setMessage("确定删除这个会话？")
            .setPositiveButton("删除", (d, w) -> http("DELETE", "/api/convs/" + id, null, (c, b) -> {
                clearDraft(id);
                if (curConv != null && curConv.equals(id)) {
                    curConv = null;
                    ++viewGeneration;
                    msgList.removeAllViews(); convTitle.setText("PhoneAgent");
                }
                refreshConvs(false);
                toast("已删除");
            }))
            .setNegativeButton("取消", null).show();
    }

    void showConvsDialog() {
        if (busy || creatingConversation || preparingSend || modeSwitching) { toast("当前操作仍在进行"); return; }
        http("GET", "/api/convs", null, (code, body) -> {
            if (code != 200) { toast("获取会话失败[" + code + "]: " + body); return; }
            final List<ConvInfo> list = new ArrayList<ConvInfo>();
            try {
                JSONArray arr = new JSONObject(body).getJSONArray("convs");
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject o = arr.getJSONObject(i);
                    ConvInfo ci = new ConvInfo();
                    ci.id = o.getString("id");
                    ci.title = o.optString("title", "新对话");
                    ci.mode = o.optString("mode", "");
                    ci.cnt = o.optInt("cnt", 0);
                    ci.updated = o.optLong("updated", 0);
                    if (ci.mode.equals(currentMode) || (ci.mode.isEmpty() && "work".equals(currentMode))) list.add(ci);
                }
            } catch (Exception ignored) {}
            showConvsDialogWithList(list);
        });
    }

    void showConvsDialogWithList(final List<ConvInfo> list) {
        final AlertDialog dlg = new AlertDialog.Builder(this).create();
        LinearLayout panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(14), dp(10), dp(14), dp(12)); panel.setBackground(roundRect(PANEL, dp(16)));

        LinearLayout titleRow = new LinearLayout(this); titleRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout titleStack = new LinearLayout(this); titleStack.setOrientation(LinearLayout.VERTICAL);
        TextView title = mkText("会话与工具", 18, TEXT); title.setTypeface(Typeface.DEFAULT_BOLD);
        TextView mode = mkText(modeLabel(currentMode) + " · 当前模式", 12, DIM);
        titleStack.addView(title); titleStack.addView(mode);
        titleRow.addView(titleStack, new LinearLayout.LayoutParams(0, -2, 1f));
        Button close = smallBtn("×"); close.setContentDescription("关闭会话面板");
        titleRow.addView(close, new LinearLayout.LayoutParams(dp(42), dp(42)));
        panel.addView(titleRow, new LinearLayout.LayoutParams(-1, -2));

        String[][] actions = "chat".equals(currentMode)
                ? new String[][]{{"角色与场景", "角色预设"}, {"长期记忆", "配额与上下文"}}
                : new String[][]{{"工作目录", "Skills"}, {"工具权限", "配额与上下文"}};
        for (String[] rowNames : actions) {
            LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.HORIZONTAL);
            for (String label : rowNames) {
                TextView card = panelActionCard(label);
                card.setOnClickListener(v -> {
                    dlg.dismiss();
                    if ("Skills".equals(label)) showSkills();
                    else if ("工具权限".equals(label)) showApprovals();
                    else if ("配额与上下文".equals(label)) showQuota();
                    else if ("工作目录".equals(label) || "角色与场景".equals(label)) showSessionSettings();
                    else if ("长期记忆".equals(label)) showMemories();
                    else if ("角色预设".equals(label)) showPresets();
                });
                LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(0, dp(48), 1f);
                cp.setMargins(dp(3), dp(3), dp(3), dp(3)); row.addView(card, cp);
            }
            panel.addView(row, new LinearLayout.LayoutParams(-1, -2));
        }

        LinearLayout secondary = new LinearLayout(this); secondary.setOrientation(LinearLayout.HORIZONTAL);
        TextView web = panelActionCard("完整网页版");
        web.setOnClickListener(v -> { dlg.dismiss(); openAdvancedWeb(); });
        TextView logs = panelActionCard("运行日志");
        logs.setOnClickListener(v -> { dlg.dismiss(); showLogs(); });
        TextView accounts = panelActionCard("ChatGPT 账户");
        accounts.setOnClickListener(v -> { dlg.dismiss(); showChatGptAccounts(); });
        LinearLayout.LayoutParams sub1 = new LinearLayout.LayoutParams(0, dp(42), 1.1f);
        sub1.setMargins(dp(3), dp(2), dp(3), dp(2)); secondary.addView(web, sub1);
        LinearLayout.LayoutParams sub2 = new LinearLayout.LayoutParams(0, dp(42), 0.75f);
        sub2.setMargins(dp(3), dp(2), dp(3), dp(2)); secondary.addView(logs, sub2);
        LinearLayout.LayoutParams sub3 = new LinearLayout.LayoutParams(0, dp(42), 1.1f);
        sub3.setMargins(dp(3), dp(2), dp(3), dp(2)); secondary.addView(accounts, sub3);
        panel.addView(secondary, new LinearLayout.LayoutParams(-1, -2));

        TextView providers = panelActionCard("API 与连接");
        providers.setOnClickListener(v -> { dlg.dismiss(); showProviderSettings(); });
        LinearLayout.LayoutParams providerLp = new LinearLayout.LayoutParams(-1, dp(40));
        providerLp.setMargins(dp(3), dp(3), dp(3), dp(3)); panel.addView(providers, providerLp);
        TextView setup = panelActionCard("Termux 安装与更新");
        setup.setOnClickListener(v -> { dlg.dismiss(); showSetupGuide(null); });
        LinearLayout.LayoutParams setupLp = new LinearLayout.LayoutParams(-1, dp(40));
        setupLp.setMargins(dp(3), 0, dp(3), dp(3)); panel.addView(setup, setupLp);

        TextView newConversation = panelActionCard("＋ 新建 " + modeLabel(currentMode) + " 会话");
        newConversation.setOnClickListener(v -> { dlg.dismiss(); newConv(); });
        LinearLayout.LayoutParams newConversationLp = new LinearLayout.LayoutParams(-1, dp(38));
        newConversationLp.setMargins(dp(3), dp(2), dp(3), dp(2)); panel.addView(newConversation, newConversationLp);

        if ("work".equals(currentMode)) {
            TextView context = panelActionCard("上下文窗口与压缩");
            context.setOnClickListener(v -> { dlg.dismiss(); showContextSettings(); });
            LinearLayout.LayoutParams contextLp = new LinearLayout.LayoutParams(-1, dp(40));
            contextLp.setMargins(dp(3), dp(2), dp(3), dp(2)); panel.addView(context, contextLp);
        }
        if (!reasoningEfforts(currentModel()).isEmpty()) {
            TextView reasoning = panelActionCard("思考强度 · " + effortLabel(currentReasoningEffort.isEmpty()
                    ? defaultReasoningEffort(currentModel(), reasoningEfforts(currentModel())) : currentReasoningEffort));
            reasoning.setOnClickListener(v -> { dlg.dismiss(); showReasoningEffortDialog(); });
            LinearLayout.LayoutParams reasoningLp = new LinearLayout.LayoutParams(-1, dp(40));
            reasoningLp.setMargins(dp(3), dp(2), dp(3), dp(2)); panel.addView(reasoning, reasoningLp);
        }

        LinearLayout currentActions = new LinearLayout(this); currentActions.setOrientation(LinearLayout.HORIZONTAL);
        TextView currentLabel = mkText(curConv == null ? "当前会话：尚未创建" : "当前会话：" + convTitle.getText(), 12, DIM);
        currentLabel.setSingleLine(true); currentLabel.setEllipsize(android.text.TextUtils.TruncateAt.END);
        currentLabel.setGravity(Gravity.CENTER_VERTICAL);
        currentActions.addView(currentLabel, new LinearLayout.LayoutParams(0, dp(38), 1f));
        TextView rename = panelActionCard("改名");
        TextView export = panelActionCard("导出");
        boolean hasCurrent = curConv != null;
        rename.setEnabled(hasCurrent); export.setEnabled(hasCurrent);
        rename.setAlpha(hasCurrent ? 1f : 0.45f); export.setAlpha(hasCurrent ? 1f : 0.45f);
        rename.setOnClickListener(v -> { if (curConv != null) { dlg.dismiss(); renameCurrentConv(); } });
        export.setOnClickListener(v -> { if (curConv != null) { dlg.dismiss(); exportCurrentConv(); } });
        LinearLayout.LayoutParams actionSmall = new LinearLayout.LayoutParams(dp(54), dp(36));
        actionSmall.setMargins(dp(3), 0, dp(3), 0); currentActions.addView(rename, actionSmall);
        LinearLayout.LayoutParams actionExport = new LinearLayout.LayoutParams(dp(58), dp(36));
        actionExport.setMargins(dp(3), 0, dp(3), 0); currentActions.addView(export, actionExport);
        panel.addView(currentActions, new LinearLayout.LayoutParams(-1, -2));

        EditText search = new EditText(this); search.setSingleLine(true);
        search.setHint("搜索 " + modeLabel(currentMode) + " 会话"); search.setHintTextColor(DIM); search.setTextColor(TEXT);
        search.setPadding(dp(12), 0, dp(12), 0); search.setBackground(roundRect(CARD, dp(10)));
        LinearLayout.LayoutParams searchLp = new LinearLayout.LayoutParams(-1, dp(42));
        searchLp.setMargins(dp(3), dp(6), dp(3), dp(5)); panel.addView(search, searchLp);

        ScrollView listScroll = new ScrollView(this); listScroll.setFillViewport(true);
        LinearLayout convList = new LinearLayout(this); convList.setOrientation(LinearLayout.VERTICAL);
        convList.setPadding(dp(2), dp(1), dp(2), dp(2)); listScroll.addView(convList, new ScrollView.LayoutParams(-1, -2));
        ArrayList<View> rows = new ArrayList<View>();
        java.text.SimpleDateFormat dateFormat = new java.text.SimpleDateFormat("MM/dd HH:mm", java.util.Locale.getDefault());
        for (ConvInfo conversation : list) {
            LinearLayout item = new LinearLayout(this); item.setOrientation(LinearLayout.VERTICAL);
            item.setPadding(dp(12), dp(8), dp(12), dp(8));
            boolean selected = conversation.id != null && conversation.id.equals(curConv);
            item.setBackground(roundRect(selected ? Color.parseColor("#21365d") : CARD, dp(10)));
            TextView name = mkText(conversation.title == null || conversation.title.isEmpty() ? "新对话" : conversation.title, 14, TEXT);
            name.setSingleLine(true); name.setEllipsize(android.text.TextUtils.TruncateAt.END);
            TextView meta = mkText(conversation.cnt + " 条消息 · " + dateFormat.format(new java.util.Date(conversation.ts())), 11, DIM);
            item.addView(name); item.addView(meta);
            LinearLayout.LayoutParams itemLp = new LinearLayout.LayoutParams(-1, -2); itemLp.setMargins(0, dp(2), 0, dp(2));
            convList.addView(item, itemLp); rows.add(item);
            item.setOnClickListener(v -> { dlg.dismiss(); loadConv(conversation.id); });
            item.setOnLongClickListener(v -> { dlg.dismiss(); deleteConv(conversation.id); return true; });
        }
        TextView empty = mkText(list.isEmpty() ? "此模式还没有会话" : "没有匹配的会话", 13, DIM);
        empty.setGravity(Gravity.CENTER); empty.setPadding(dp(12), dp(16), dp(12), dp(16));
        convList.addView(empty); empty.setVisibility(list.isEmpty() ? View.VISIBLE : View.GONE);
        search.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            public void onTextChanged(CharSequence s, int start, int before, int count) {}
            public void afterTextChanged(Editable s) {
                String query = s.toString().trim().toLowerCase(java.util.Locale.ROOT);
                boolean any = false;
                for (int i = 0; i < list.size(); i++) {
                    ConvInfo c = list.get(i);
                    String haystack = ((c.title == null ? "" : c.title) + " " + c.cnt).toLowerCase(java.util.Locale.ROOT);
                    boolean match = query.isEmpty() || haystack.contains(query);
                    rows.get(i).setVisibility(match ? View.VISIBLE : View.GONE); any |= match;
                }
                empty.setVisibility(any ? View.GONE : View.VISIBLE);
                empty.setText(list.isEmpty() ? "此模式还没有会话" : "没有匹配的会话");
            }
        });
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(-1, 0, 1f);
        scrollLp.setMargins(0, dp(1), 0, 0); panel.addView(listScroll, scrollLp);

        close.setOnClickListener(v -> dlg.dismiss());
        dlg.setView(panel);
        dlg.setCanceledOnTouchOutside(true);
        dlg.show();
        if (dlg.getWindow() != null) {
            android.util.DisplayMetrics dm = getResources().getDisplayMetrics();
            int width = Math.min((int) (dm.widthPixels * 0.86f), dp(420));
            dlg.getWindow().setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
            dlg.getWindow().setGravity(Gravity.START | Gravity.TOP);
            dlg.getWindow().setDimAmount(0.48f);
            dlg.getWindow().setLayout(width, ViewGroup.LayoutParams.MATCH_PARENT);
            panel.setMinimumHeight(Math.max(dp(500), dm.heightPixels - dp(100)));
        }
    }

    void showReasoningEffortDialog() {
        List<String> choices = reasoningEfforts(currentModel());
        if (choices.isEmpty()) { toast("当前模型未提供可选思考强度"); return; }
        String[] labels = new String[choices.size()];
        for (int i = 0; i < choices.size(); i++) labels[i] = effortLabel(choices.get(i));
        String current = choices.contains(currentReasoningEffort) ? currentReasoningEffort
                : defaultReasoningEffort(currentModel(), choices);
        new AlertDialog.Builder(this).setTitle("思考强度 · " + labelForModel(currentModel()))
                .setSingleChoiceItems(labels, choices.indexOf(current), (dialog, which) -> {
                    if (which >= 0 && which < choices.size()) persistReasoningEffort(choices.get(which));
                    dialog.dismiss();
                }).setNegativeButton("关闭", null).show();
    }

    TextView panelActionCard(String label) {
        TextView card = mkText(label, 13, TEXT); card.setGravity(Gravity.CENTER);
        card.setPadding(dp(6), dp(4), dp(6), dp(4)); card.setBackground(roundRect(CARD, dp(10)));
        card.setClickable(true); card.setFocusable(true); return card;
    }

    void renameCurrentConv() {
        if (curConv == null) { toast("没有可改名的会话"); return; }
        EditText editor = new EditText(this);
        editor.setText(convTitle.getText());
        editor.setSingleLine(true);
        new AlertDialog.Builder(this).setTitle("会话名称").setView(editor)
            .setPositiveButton("保存", (d, w) -> {
                String title = editor.getText().toString().trim();
                if (title.isEmpty()) { toast("名称不能为空"); return; }
                try {
                    JSONObject data = new JSONObject(); data.put("title", title);
                    String id = curConv;
                    http("POST", "/api/convs/" + id + "/rename", data.toString(), (c, b) -> {
                        if (c == 200) { convTitle.setText(title); refreshConvs(false); }
                        else toast("改名失败: " + c);
                    });
                } catch (Exception e) { toast("改名失败"); }
            }).setNegativeButton("取消", null).show();
    }

    void exportCurrentConv() {
        if (curConv == null) { toast("没有可导出的会话"); return; }
        final String id = curConv;
        http("GET", "/api/convs/" + id + "/messages", null, (c, body) -> {
            if (c != 200) { toast("导出失败: " + c); return; }
            try {
                JSONArray arr = new JSONObject(body).getJSONArray("messages");
                StringBuilder out = new StringBuilder("# ").append(convTitle.getText()).append("\n\n");
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject m = arr.getJSONObject(i);
                    String role = m.optString("role");
                    String text = extractPartsText(m.optString("content", ""));
                    out.append("## ").append("user".equals(role) ? "用户" : "Agent").append("\n\n")
                       .append(text).append("\n\n");
                }
                Intent share = new Intent(Intent.ACTION_SEND);
                share.setType("text/plain"); share.putExtra(Intent.EXTRA_SUBJECT, convTitle.getText().toString());
                share.putExtra(Intent.EXTRA_TEXT, out.toString());
                startActivity(Intent.createChooser(share, "导出会话"));
            } catch (Exception e) { toast("导出失败: " + safeMessage(e)); }
        });
    }

    void showLogs() {
        http("GET", "/api/logs", null, (c, body) -> {
            if (c != 200) { toast("日志暂不可用: " + c); return; }
            StringBuilder out = new StringBuilder();
            try {
                JSONArray logs = new JSONObject(body).optJSONArray("logs");
                if (logs != null) for (int i = Math.max(0, logs.length() - 80); i < logs.length(); i++) {
                    JSONObject row = logs.optJSONObject(i);
                    if (row == null) continue;
                    long time = row.optLong("time", 0);
                    String stamp = time > 0 ? new java.text.SimpleDateFormat("MM-dd HH:mm:ss", java.util.Locale.getDefault())
                            .format(new java.util.Date(time * 1000L)) : "--:--:--";
                    String event = friendlyLogEvent(row.optString("event", "event"));
                    out.append(stamp).append("  ").append(event);
                    String model = row.optString("model", "");
                    String tool = row.optString("tool", "");
                    String status = row.optString("status", "");
                    if (!model.isEmpty()) out.append(" · ").append(model);
                    if (!tool.isEmpty()) out.append(" · ").append(friendlyToolName(tool));
                    if (!status.isEmpty()) out.append(" · ").append(status);
                    if (row.has("code")) out.append(" · ").append(row.optString("code"));
                    out.append('\n');
                }
            } catch (Exception ignored) { out.append(body); }
            ScrollView sc = new ScrollView(this);
            TextView tv = mkText(out.length() == 0 ? "暂无日志" : out.toString(), 12, TEXT);
            tv.setTypeface(Typeface.MONOSPACE); tv.setPadding(dp(12), dp(8), dp(12), dp(8));
            sc.addView(tv);
            new AlertDialog.Builder(this).setTitle("最近运行日志").setView(sc).setPositiveButton("关闭", null).show();
        });
    }

    String friendlyLogEvent(String event) {
        if ("run_started".equals(event)) return "开始执行";
        if ("run_completed".equals(event)) return "执行完成";
        if ("run_failed".equals(event)) return "执行失败";
        if ("http_error".equals(event)) return "服务请求错误";
        if ("tool_started".equals(event)) return "开始调用工具";
        if ("tool_completed".equals(event)) return "工具返回结果";
        return event;
    }

    // ---------------- 消息渲染 ----------------

    TextView mkText(String s, int size, int color) {
        TextView tv = new TextView(this);
        tv.setText(s); tv.setTextSize(size); tv.setTextColor(color);
        return tv;
    }

    void saveDraft() {
        if (curConv == null || input == null) return;
        getPreferences(MODE_PRIVATE).edit().putString("draft_" + curConv,
                input.getText().toString()).apply();
    }

    void restoreDraft(String id) {
        if (input == null) return;
        String draft = getPreferences(MODE_PRIVATE).getString("draft_" + id, "");
        input.setText(draft);
        input.setSelection(input.getText().length());
    }

    void clearDraft(String id) {
        if (id != null) getPreferences(MODE_PRIVATE).edit().remove("draft_" + id).apply();
    }

    String safeMessage(Exception e) {
        return e == null || e.getMessage() == null ? "未知错误" : e.getMessage();
    }

    void addInlineStatus(String text) {
        TextView tv = mkText(text, 13, DIM);
        tv.setGravity(Gravity.CENTER);
        tv.setPadding(dp(12), dp(18), dp(12), dp(18));
        msgList.addView(tv, new LinearLayout.LayoutParams(-1, -2));
    }

    LinearLayout addUserMsg(String text) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setGravity(Gravity.END);
        wrap.setPadding(0, dp(4), 0, dp(4));
        wrap.setTag("user");
        TextView tv = mkText(text, 15, TEXT);
        tv.setBackground(roundRect(USER_BG, dp(14)));
        tv.setPadding(dp(12), dp(9), dp(12), dp(9));
        final LinearLayout fw = wrap;
        tv.setOnClickListener(v -> scroller.smoothScrollTo(0, Math.max(0, fw.getTop() - dp(6))));
        wrap.addView(tv);
        msgList.addView(wrap, new LinearLayout.LayoutParams(-1, -2));
        scrollBottom();
        return wrap;
    }

    LinearLayout addAssistantShell() {
        LinearLayout bubble = new LinearLayout(this);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setBackground(roundRect(PANEL, dp(14)));
        bubble.setPadding(dp(12), dp(10), dp(12), dp(10));
        LinearLayout wrap = new LinearLayout(this);
        wrap.setPadding(0, dp(4), 0, dp(4));
        wrap.setTag("assistant");
        if (userOnly) wrap.setVisibility(View.GONE);
        wrap.addView(bubble, new LinearLayout.LayoutParams(-1, -2));
        msgList.addView(wrap);
        return bubble;
    }

    TextView addMdText(LinearLayout bubble, String text) {
        TextView tv = new TextView(this);
        tv.setTextSize(15); tv.setTextColor(TEXT);
        tv.setLineSpacing(dp(2), 1f);
        setMd(tv, text);
        tv.setPadding(0, dp(2), 0, dp(2));
        tv.setOnLongClickListener(v -> {
            String copy = tv.getText().toString();
            if (copy.isEmpty()) return false;
            try {
                ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                clipboard.setPrimaryClip(ClipData.newPlainText("PhoneAgent 回复", copy));
                toast("已复制回复（含代码文本）");
                return true;
            } catch (Exception e) { toast("复制失败"); return true; }
        });
        bubble.addView(tv, new LinearLayout.LayoutParams(-1, -2));
        return tv;
    }

    static class ReasoningBlock {
        TextView head, body;
        StringBuilder buf = new StringBuilder();
        void refresh(boolean thinking) {
            head.setText((thinking ? "🧠 思考中…" : "🧠 思考过程") + " ▸");
            body.setText(buf.toString());
        }
        void toggle() {
            body.setVisibility(body.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
        }
    }

    ReasoningBlock addReasoning(LinearLayout bubble, boolean open) {
        final ReasoningBlock rb = new ReasoningBlock();
        rb.head = new TextView(this);
        rb.head.setTextSize(12); rb.head.setTextColor(DIM);
        rb.head.setTypeface(Typeface.DEFAULT_BOLD);
        rb.head.setPadding(0, dp(4), 0, dp(2));
        rb.body = new TextView(this);
        rb.body.setTextSize(13); rb.body.setTextColor(Color.parseColor("#9aa3b5"));
        rb.body.setVisibility(open ? View.VISIBLE : View.GONE);
        rb.head.setOnClickListener(v -> rb.toggle());
        bubble.addView(rb.head);
        bubble.addView(rb.body, new LinearLayout.LayoutParams(-1, -2));
        return rb;
    }

    LinearLayout addToolCard(LinearLayout bubble, String name, String args) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(roundRect(Color.parseColor("#0f141f"), dp(10)));
        card.setPadding(dp(10), dp(7), dp(10), dp(7));
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(-1, -2) {{ topMargin = dp(6); }};
        TextView head = new TextView(this);
        head.setTextSize(13);
        String shownName = friendlyToolName(name);
        SpannableString ss = new SpannableString("⚙ " + shownName + "  ⏳  ▸");
        ss.setSpan(new StyleSpan(Typeface.BOLD), 2, 2 + shownName.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        head.setText(ss);
        head.setTextColor(TEXT);
        card.addView(head);
        head.setTag(Boolean.FALSE);
        head.setContentDescription("点击查看工具详情");
        head.setOnClickListener(v -> {
            boolean expanded = !Boolean.TRUE.equals(head.getTag());
            head.setTag(Boolean.valueOf(expanded));
            head.setText(head.getText().toString().replace(expanded ? "▸" : "▾", expanded ? "▾" : "▸"));
            for (int index = 1; index < card.getChildCount(); index++)
                card.getChildAt(index).setVisibility(expanded ? View.VISIBLE : View.GONE);
        });
        if (args != null && !args.equals("null") && !args.isEmpty()) {
            TextView av = mkText(shortArgs(args), 11, DIM);
            av.setTypeface(Typeface.MONOSPACE);
            card.addView(av);
            av.setVisibility(View.GONE);
        }
        bubble.addView(card, cp);
        scrollBottom();
        return card;
    }

    String friendlyToolName(String name) {
        if ("run_command".equals(name)) return "执行命令";
        if ("read_file".equals(name)) return "读取文件";
        if ("write_file".equals(name)) return "写入文件";
        if ("list_files".equals(name)) return "查看文件列表";
        if ("delete_file".equals(name)) return "删除文件";
        if ("web_search".equals(name)) return "网页搜索";
        if ("open_in_cxxdroid".equals(name)) return "打开 Cxxdroid";
        if ("view_image".equals(name)) return "查看图片";
        if ("notify".equals(name)) return "发送通知";
        return name;
    }

    String shortArgs(String argsJson) {
        try {
            JSONObject o = new JSONObject(argsJson);
            if (o.has("command")) return "$ " + o.getString("command");
            if (o.has("path")) return "path: " + o.getString("path") +
                    (o.has("content") ? "\ncontent: " + o.getString("content").length() + " 字符" : "");
            if (o.has("query")) return "🔎 " + o.getString("query");
        } catch (Exception ignored) {}
        return argsJson;
    }

    void finishToolCard(LinearLayout card, boolean ok, String summary) {
        TextView res = mkText(summary == null || summary.isEmpty() ? "(无输出)" : summary, 12,
                Color.parseColor("#c9d2e3"));
        res.setTypeface(Typeface.MONOSPACE);
        res.setBackground(roundRect(Color.parseColor("#0d1120"), dp(8)));
        res.setPadding(dp(8), dp(6), dp(8), dp(6));
        card.addView(res, new LinearLayout.LayoutParams(-1, -2) {{ topMargin = dp(4); }});
        TextView head = (TextView) card.getChildAt(0);
        if (!ok) head.setTag(Boolean.TRUE);
        for (int index = 1; index < card.getChildCount(); index++)
            card.getChildAt(index).setVisibility(Boolean.TRUE.equals(head.getTag()) ? View.VISIBLE : View.GONE);
        String txt = head.getText().toString().replace("⏳", ok ? "✓" : "✗");
        head.setText(txt);
        scrollBottom();
    }

    void addError(LinearLayout bubble, String err) {
        TextView tv = mkText("错误: " + err, 13, RED);
        tv.setPadding(0, dp(4), 0, 0);
        bubble.addView(tv);
    }

    void renderHistoryAssistant(String content, JSONObject meta) {
        LinearLayout bubble = addAssistantShell();
        String reasoning = meta.optString("reasoning", "");
        if (!reasoning.isEmpty()) {
            ReasoningBlock rb = addReasoning(bubble, false);
            rb.buf.append(reasoning);
            rb.refresh(false);
            rb.body.setVisibility(View.GONE);
        }
        JSONArray tcs = meta.optJSONArray("tool_calls");
        JSONObject results = meta.optJSONObject("tool_results");
        if (tcs != null) {
            for (int i = 0; i < tcs.length(); i++) {
                JSONObject tc = tcs.optJSONObject(i);
                if (tc == null) continue;
                JSONObject fn = tc.optJSONObject("function");
                if (fn == null) continue;
                String args = fn.optString("arguments", "{}");
                LinearLayout card = addToolCard(bubble, fn.optString("name", "?"), args);
                String rid = tc.optString("id", "");
                Object rawResult = results == null ? null : results.opt(rid);
                boolean ok = true;
                String out = "(无输出)";
                if (rawResult instanceof JSONObject) {
                    JSONObject result = (JSONObject) rawResult;
                    ok = result.optBoolean("ok", true);
                    out = result.optString("output", result.optString("summary", "(无输出)"));
                } else if (rawResult != null && rawResult != JSONObject.NULL) {
                    out = String.valueOf(rawResult);
                }
                finishToolCard(card, ok, out);
            }
        }
        if (content != null && !content.isEmpty() && !content.equals("(无回复)")
                && !content.equals("[仅调用了工具，无正文]") && !content.equals("（仅思考，无正文）"))
            addMdText(bubble, content);
        String error = meta.optString("error", "");
        String runStatus = meta.optString("status", "");
        if (error.isEmpty() && ("error".equalsIgnoreCase(runStatus) || "interrupted".equalsIgnoreCase(runStatus)))
            error = "interrupted".equalsIgnoreCase(runStatus) ? "本轮连接中断" : "本轮执行失败";
        boolean retryable = meta.optBoolean("retryable", false);
        if (!error.isEmpty()) addError(bubble, error);
        if (!runStatus.isEmpty()) bubble.addView(mkText("状态：" + runStatus, 11, DIM));
        if (retryable) addHistoryRetry(bubble, meta);
        scrollBottom();
    }

    void addHistoryRetry(LinearLayout sourceBubble, JSONObject meta) {
        StreamCtx retryCtx = new StreamCtx();
        retryCtx.convId = curConv;
        retryCtx.model = meta.optString("model", currentModel());
        retryCtx.expectedRunId = meta.isNull("run_id") ? null : meta.optString("run_id", null);
        retryCtx.retryOnly = true;
        retryCtx.generation = viewGeneration;
        Button retryBtn = smallBtn("↻ 重试本轮");
        retryBtn.setTextColor(Color.WHITE); retryBtn.setBackground(roundRect(ACCENT, dp(10)));
        retryBtn.setOnClickListener(v -> preflightRetry(retryCtx, retryBtn));
        sourceBubble.addView(retryBtn, new LinearLayout.LayoutParams(-2, dp(42)) {{ topMargin = dp(7); }});
    }

    void renderPersistedRun(JSONObject run) {
        LinearLayout bubble = addAssistantShell();
        String error = run.optString("error", "本轮未完成");
        addError(bubble, error);
        String status = run.optString("status", "");
        if (!status.isEmpty()) bubble.addView(mkText("状态：" + status, 11, DIM));
        addHistoryRetry(bubble, run);
    }

    // 用户在看历史（stick=false）时不强制滚动；在底部时跟随最新内容
    void scrollBottom() {
        if (stick) scrollBottomForce();
    }

    void scrollBottomForce() {
        runOnUiThread(() -> {
            suppressScroll = true;
            stick = true;
            if (jumpBtn != null) jumpBtn.setVisibility(View.GONE);
            scroller.scrollTo(0, msgList.getHeight());
            msgList.post(() -> scroller.scrollTo(0, msgList.getHeight()));
            scroller.postDelayed(() -> suppressScroll = false, 120);
        });
    }

    void toggleUserOnly() {
        userOnly = !userOnly;
        filterBtn.setTextColor(userOnly ? Color.WHITE : TEXT);
        filterBtn.setBackground(roundRect(userOnly ? USER_BG : CARD, dp(10)));
        applyUserFilter();
        toast(userOnly ? "只显示用户消息（点消息可跳转）" : "显示全部消息");
    }

    void applyUserFilter() {
        for (int i = 0; i < msgList.getChildCount(); i++) {
            View ch = msgList.getChildAt(i);
            boolean user = "user".equals(ch.getTag());
            ch.setVisibility(userOnly && !user ? View.GONE : View.VISIBLE);
        }
    }

    // ---------------- 发送与流式 ----------------

    // 一次流式会话的渲染上下文（时间线：思考块/文本段/工具卡按产生顺序排列）
    static class StreamCtx {
        LinearLayout bubble;
        String convId, model;
        String originalText, originalImage, originalDraft;
        String expectedRunId;
        long baselineUserId;
        long generation;
        boolean failed;
        boolean retryOnly;
        TextView ph;          // "思考中…" 占位符
        TextView curText;     // 当前文本段
        StringBuilder curBuf = new StringBuilder();
        final Object displayLock = new Object();
        final ArrayDeque<StreamDisplayEvent> displayQueue = new ArrayDeque<StreamDisplayEvent>();
        final StringBuilder receivedText = new StringBuilder(), receivedReasoning = new StringBuilder();
        long lastDrainNanos, lastMarkdownMs;
        double charCredit;
        int markdownRenders;
        boolean drainScheduled, networkDone, displayFinished, fastForward, cancelled, receivedDone;
        LinearLayout drainFooter;
        TextView drainStatus;
        Button drainSkip;
        HttpURLConnection connection;
        ReasoningBlock curR;  // 当前思考块
        final java.util.HashMap<String, LinearLayout> cards = new java.util.HashMap<String, LinearLayout>();
    }

    static class StreamDisplayEvent {
        String type;
        JSONObject json;
        StringBuilder text;
        int offset;
        boolean sealed;
    }

    void closeTextSeg(StreamCtx s) {
        s.curText = null;
        s.curBuf = new StringBuilder();
    }

    void closeReasoningSeg(StreamCtx s) {
        if (s.curR != null) { s.curR.refresh(false); s.curR = null; }
    }

    void dropPh(StreamCtx s) {
        if (s.ph != null && s.ph.getParent() != null) s.bubble.removeView(s.ph);
    }

    void sendMessage() {
        if (modeSwitching) return;
        final String draftSnapshot = input.getText().toString();
        final String txt = draftSnapshot.trim();
        final String sendImg = pendingImg;
        if ((txt.isEmpty() && sendImg == null) || busy || creatingConversation || preparingSend) return;
        if (curConv == null) { createConversationForSend(txt, sendImg, draftSnapshot); return; }
        submitOutgoing(txt, sendImg, draftSnapshot);
    }

    void createConversationForSend(final String txt, final String image, final String draftSnapshot) {
        creatingConversation = true;
        input.setEnabled(false); sendBtn.setEnabled(false); pickBtn.setEnabled(false);
        http("POST", "/api/convs", createConvBody(), (code, body) -> {
            creatingConversation = false;
            input.setEnabled(true); sendBtn.setEnabled(true); pickBtn.setEnabled(true);
            if (code != 201 && code != 200) { toast("创建会话失败: " + body); return; }
            try { curConv = new JSONObject(body).getString("id"); }
            catch (Exception e) { curConv = null; }
            if (curConv == null || curConv.isEmpty()) { toast("创建会话失败: 响应无效"); return; }
            ++viewGeneration;
            pendingImg = image;
            msgList.removeAllViews(); convTitle.setText("新对话");
            saveDraft();
            persistSettings("model", currentModel());
            refreshConvs(false);
            submitOutgoing(txt, image, draftSnapshot);
        });
    }

    void submitOutgoing(String txt, String image, String draftSnapshot) {
        if (preparingSend || busy) return;
        preparingSend = true;
        sendBtn.setEnabled(false);
        final String convId = curConv;
        final long generation = viewGeneration;
        http("GET", "/api/convs/" + convId + "/messages", null, (code, body) -> {
            preparingSend = false;
            sendBtn.setEnabled(true);
            if (generation != viewGeneration || !convId.equals(curConv)) return;
            if (code != 200) { toast("无法确认会话状态，请稍后重试"); return; }
            long baseline = lastRealUserId(body);
            postOutgoingAfterPreflight(txt, image, draftSnapshot, baseline);
        });
    }

    void postOutgoingAfterPreflight(String txt, String image, String draftSnapshot, long baselineUserId) {
        LinearLayout userWrap = addUserMsg(txt.isEmpty() ? "（图片）" : txt);
        final String sendImg = image;
        addUserImage(userWrap, sendImg);
        final StreamCtx s = new StreamCtx();
        s.bubble = addAssistantShell();
        s.convId = curConv;
        s.model = currentModel();
        s.originalText = txt;
        s.originalImage = sendImg;
        s.originalDraft = draftSnapshot;
        s.baselineUserId = baselineUserId;
        s.generation = viewGeneration;
        startStream(s, txt, sendImg, false);
    }

    boolean isCurrentStream(StreamCtx s) {
        return s != null && s.generation == viewGeneration && s.convId != null
                && s.convId.equals(curConv) && s.bubble != null
                && s.bubble.getParent() != null && s.bubble.getParent().getParent() == msgList;
    }

    void setBusy(boolean value) {
        busy = value;
        sendBtn.setVisibility(value ? View.GONE : View.VISIBLE);
        stopBtn.setVisibility(value ? View.VISIBLE : View.GONE);
        input.setEnabled(!modeSwitching);
        pickBtn.setEnabled(!value && !modeSwitching);
        // A running turn stays bound to its conversation; prevent accidental UI navigation.
        filterBtn.setEnabled(!value && !modeSwitching);
        modelSel.setEnabled(!value && !modeSwitching);
        reasoningSel.setEnabled(!value && !modeSwitching);
        modeBtn.setEnabled(!value && !modeSwitching);
        menuBtn.setEnabled(!value && !modeSwitching);
        newConvBtn.setEnabled(!value && !modeSwitching);
        sendBtn.setEnabled(!value && !modeSwitching);
        setApprovalActionsEnabled(msgList, !value);
    }

    JSONObject lastRealUser(String body) {
        try {
            JSONArray messages = new JSONObject(body).optJSONArray("messages");
            if (messages == null) return null;
            for (int i = messages.length() - 1; i >= 0; i--) {
                JSONObject m = messages.optJSONObject(i);
                if (m == null || !"user".equals(m.optString("role"))) continue;
                JSONObject meta = safeObj(m.optString("meta", "{}"));
                if (!meta.optBoolean("injected", false)) return m;
            }
        } catch (Exception ignored) {}
        return null;
    }

    long lastRealUserId(String body) {
        JSONObject user = lastRealUser(body);
        return user == null ? 0 : user.optLong("id", 0);
    }

    boolean hasImagePart(String content) {
        if (content == null || !content.trim().startsWith("[")) return false;
        try {
            JSONArray parts = new JSONArray(content);
            for (int i = 0; i < parts.length(); i++) {
                JSONObject part = parts.optJSONObject(i);
                if (part != null && "image_url".equals(part.optString("type"))) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    boolean matchesAttempt(JSONObject user, StreamCtx attempt) {
        if (user == null || attempt == null || user.optLong("id", 0) <= attempt.baselineUserId) return false;
        String content = user.optString("content", "");
        boolean hasImage = hasImagePart(content);
        if ((attempt.originalImage != null) != hasImage) return false;
        String actualText = extractPartsText(content).trim();
        String expectedText = attempt.originalText == null ? "" : attempt.originalText.trim();
        if (expectedText.isEmpty()) return hasImage;
        return expectedText.equals(actualText);
    }

    void preflightRetry(final StreamCtx attempt, final Button button) {
        if (busy || preparingSend || attempt.convId == null || !attempt.convId.equals(curConv)
                || attempt.generation != viewGeneration) return;
        preparingSend = true;
        button.setEnabled(false); button.setText("正在检查…");
        http("GET", "/api/convs/" + attempt.convId + "/messages", null, (code, body) -> {
            preparingSend = false;
            if (attempt.generation != viewGeneration || !attempt.convId.equals(curConv)) return;
            if (code != 200) { button.setEnabled(true); button.setText("↻ 重试本轮"); toast("暂时无法确认任务状态"); return; }
            JSONObject payload = safeObj(body);
            JSONObject run = payload.optJSONObject("run");
            JSONObject user = lastRealUser(body);
            if (attempt.retryOnly) {
                boolean sameRun = run != null && run.optBoolean("retryable", false)
                        && (attempt.expectedRunId == null || run.isNull("run_id")
                            || attempt.expectedRunId.equals(run.optString("run_id", "")));
                if (sameRun) launchRetry(attempt, user == null ? 0 : user.optLong("id", 0), run);
                else showPreflightResult(attempt, button,
                        "这条失败记录已变化或已结束，没有重复执行。", false);
                return;
            }
            boolean accepted = matchesAttempt(user, attempt);
            String state = run == null ? "" : run.optString("status", "");
            if ("running".equals(state)) {
                showPreflightResult(attempt, button, "服务仍在处理会话中的任务，请稍后查看。", false);
                button.setEnabled(true); button.setText("稍后再检查");
            } else if (accepted && run != null && run.optBoolean("retryable", false)) {
                launchRetry(attempt, user.optLong("id", 0), run);
            } else if (accepted) {
                if ("completed".equals(state)) clearSentDraftIfUnchanged(attempt);
                showPreflightResult(attempt, button,
                        "服务器已收到这条消息，当前状态为“" + (state.isEmpty() ? "未知" : state) + "”，未重复执行。", false);
            } else {
                long baseline = user == null ? 0 : user.optLong("id", 0);
                StreamCtx resend = newAttemptBubble(attempt, baseline);
                startStream(resend, resend.originalText, resend.originalImage, false);
            }
        });
    }

    StreamCtx newAttemptBubble(StreamCtx source, long baselineUserId) {
        StreamCtx next = new StreamCtx();
        next.convId = source.convId; next.model = source.model; next.generation = source.generation;
        next.originalText = source.originalText; next.originalImage = source.originalImage;
        next.originalDraft = source.originalDraft;
        next.baselineUserId = baselineUserId; next.bubble = addAssistantShell();
        return next;
    }

    void launchRetry(StreamCtx source, long acceptedUserId, JSONObject run) {
        StreamCtx next = newAttemptBubble(source, acceptedUserId);
        next.expectedRunId = run == null || run.isNull("run_id") ? null : run.optString("run_id", null);
        next.retryOnly = true;
        startStream(next, null, null, true);
    }

    void showPreflightResult(StreamCtx attempt, Button button, String message, boolean clear) {
        button.setEnabled(false); button.setText("状态已确认");
        if (clear) clearSentDraftIfUnchanged(attempt);
        attempt.bubble.addView(mkText(message, 12, DIM));
        scrollBottom();
    }

    void clearSentDraftIfUnchanged(StreamCtx attempt) {
        if (attempt.originalDraft != null && input.getText().toString().equals(attempt.originalDraft))
            input.setText("");
        if (attempt.originalImage != null && attempt.originalImage.equals(pendingImg)) pendingImg = null;
        saveDraft();
    }

    void startStream(final StreamCtx s, String message, String image, boolean retry) {
        if (busy || s == null) return;
        stopping = false;
        activeStream = s; s.lastDrainNanos = System.nanoTime();
        setBusy(true);
        stick = true;
        dropPh(s);
        closeReasoningSeg(s);
        closeTextSeg(s);
        s.ph = mkText("思考中…", 15, DIM);
        s.bubble.addView(s.ph);
        scrollBottom();
        final String reqMessage = message;
        final String reqImage = image;
        final boolean retryTurn = retry;
        final Thread th = new Thread(() -> {
            boolean sawDone = false;
            try {
                JSONObject req = new JSONObject();
                req.put("conv_id", s.convId);
                req.put("model", s.model);
                if (retryTurn) req.put("retry", true);
                else req.put("message", reqMessage);
                if (!retryTurn && reqImage != null) {
                    JSONArray imgs = new JSONArray();
                    imgs.put(reqImage);
                    req.put("images", imgs);
                }
                HttpURLConnection c = (HttpURLConnection) new URL(BASE + "/api/chat").openConnection();
                c.setRequestMethod("POST");
                c.setConnectTimeout(10000);
                c.setReadTimeout(600000);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                s.connection = c; streamConn = c;
                OutputStream os = c.getOutputStream();
                os.write(req.toString().getBytes(StandardCharsets.UTF_8));
                os.close();
                int code = c.getResponseCode();
                InputStream stream = code >= 400 ? c.getErrorStream() : c.getInputStream();
                if (stream == null) throw new java.io.IOException("HTTP " + code + " 无响应内容");
                if (code >= 400) {
                    BufferedReader er = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
                    StringBuilder eb = new StringBuilder(); String el;
                    while ((el = er.readLine()) != null && eb.length() < 1200) eb.append(el);
                    er.close(); c.disconnect();
                    throw new java.io.IOException("HTTP " + code + (eb.length() > 0 ? "：" + eb : ""));
                }
                BufferedReader r = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
                String line;
                while ((line = r.readLine()) != null) {
                    if (!line.startsWith("data:")) continue;
                    String payload = line.substring(5).trim();
                    if (payload.isEmpty()) continue;
                    handleEvent(payload, s);
                    try {
                        JSONObject e = new JSONObject(payload);
                        if ("done".equals(e.optString("type"))) sawDone = true;
                    } catch (Exception ignored) {}
                }
                r.close(); c.disconnect();
            } catch (Exception e) {
                s.failed = true;
                try { if (s.connection != null) s.connection.disconnect(); } catch (Exception ignored) {}
                final String msg = e.getMessage() == null ? e.toString() : e.getMessage();
                boolean wasStopped = s.cancelled || stopping;
                enqueueStreamUiEvent(s, makeStreamEvent("display_error", wasStopped ? "已停止" : msg,
                        !wasStopped && transportRetryable(msg)));
            }
            final boolean completed = sawDone;
            s.receivedDone = completed; s.networkDone = true;
            JSONObject terminal = new JSONObject();
            try { terminal.put("type", "transport_finish"); terminal.put("completed", completed); terminal.put("cancelled", s.cancelled || stopping); }
            catch (Exception ignored) {}
            enqueueStreamUiEvent(s, terminal);
            runOnUiThread(() -> {
                if (streamConn == s.connection) streamConn = null;
                setBusy(false);
                if (isCurrentStream(s) && hasPendingStreamDisplay(s)) showStreamDrainNotice(s);
                refreshConvs(false);
            });
        });
        th.start();
    }

    boolean transportRetryable(String message) {
        if (message == null || !message.startsWith("HTTP ")) return true;
        try {
            int code = Integer.parseInt(message.substring(5, 8));
            return code == 408 || code == 429 || code >= 500;
        } catch (Exception ignored) { return true; }
    }

    void showFailure(StreamCtx s, String error, boolean retryable, String status, JSONObject usage) {
        if (!isCurrentStream(s)) return;
        for (LinearLayout pending : new ArrayList<LinearLayout>(s.cards.values()))
            finishToolCard(pending, false, "调用中断，未收到工具结果");
        s.cards.clear();
        lastFailureBubble = s.bubble;
        failedConv = s.convId; failedModel = s.model;
        addError(s.bubble, error == null || error.isEmpty() ? "请求失败" : error);
        String details = "";
        if (status != null && !status.isEmpty()) details = "状态：" + status;
        if (usage != null && usage.length() > 0) details += (details.isEmpty() ? "" : " · ") + "用量：" + usageSummary(usage);
        if (!details.isEmpty()) s.bubble.addView(mkText(details, 11, DIM));
        if (retryable) {
            Button retryBtn = smallBtn("↻ 重试本轮");
            retryBtn.setTextColor(Color.WHITE); retryBtn.setBackground(roundRect(ACCENT, dp(10)));
            retryBtn.setOnClickListener(v -> preflightRetry(s, retryBtn));
            s.bubble.addView(retryBtn, new LinearLayout.LayoutParams(-2, dp(42)) {{ topMargin = dp(7); }});
        }
        scrollBottom();
    }

    String usageSummary(JSONObject usage) {
        int in = usage.optInt("prompt_tokens", usage.optInt("input_tokens", 0));
        int out = usage.optInt("completion_tokens", usage.optInt("output_tokens", 0));
        if (in == 0 && out == 0) return usage.toString();
        return in + " 输入 / " + out + " 输出 tokens";
    }

    void stopStream() {
        stopping = true;
        if (activeStream != null) activeStream.cancelled = true;
        try {
            HttpURLConnection conn = activeStream == null ? streamConn : activeStream.connection;
            if (conn != null) conn.disconnect();
        } catch (Exception ignored) {}
        try {
            JSONObject body = new JSONObject(); body.put("conv_id", curConv);
            http("POST", "/api/stop", body.toString(), (c, b) -> {});
        } catch (Exception ignored) {}
    }

    String currentModel() {
        int p = modelSel.getSelectedItemPosition();
        List<String> available = modelsForMode(currentMode);
        if (p >= 0 && p < available.size()) return available.get(p);
        return available.isEmpty() ? "" : available.get(0);
    }

    // ---------- 图片附件 ----------
    void pickImage() {
        if (modeSwitching) { toast("模式切换期间暂不可选图片"); return; }
        if (!visionModels.contains(currentModel())) { toast("当前模型不支持图片"); return; }
        try { startActivityForResult(new Intent(Intent.ACTION_GET_CONTENT).setType("image/*"), REQ_PICK_IMG); }
        catch (Exception e) { toast("无法打开相册: " + e.getMessage()); }
    }

    @Override
    protected void onActivityResult(int reqCode, int resultCode, Intent data) {
        super.onActivityResult(reqCode, resultCode, data);
        if (reqCode != REQ_PICK_IMG || resultCode != RESULT_OK || data == null || data.getData() == null) return;
        final Uri uri = data.getData();
        final Thread th = new Thread(() -> {
            try {
                InputStream is = getContentResolver().openInputStream(uri);
                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                byte[] buf = new byte[8192]; int n; while ((n = is.read(buf)) != -1) bos.write(buf, 0, n);
                is.close();
                if (bos.size() > 20 * 1024 * 1024) { runOnUiThread(() -> toast("图片过大")); return; }
                String b64 = Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP);
                String mime = getContentResolver().getType(uri);
                if (mime == null) mime = "image/jpeg";
                pendingImg = "data:" + mime + ";base64," + b64;
                runOnUiThread(() -> toast("已附加图片，发送即可"));
            } catch (Exception e) {
                runOnUiThread(() -> toast("读取图片失败: " + e.getMessage()));
            }
        });
        th.start();
    }

    void addUserImage(LinearLayout wrap, String dataUrl) {
        if (dataUrl == null) return;
        try {
            String b64 = dataUrl.substring(dataUrl.indexOf(',') + 1);
            byte[] d = Base64.decode(b64, Base64.DEFAULT);
            Bitmap bm = BitmapFactory.decodeByteArray(d, 0, d.length);
            if (bm == null) return;
            ImageView iv = new ImageView(this);
            iv.setImageBitmap(bm);
            iv.setAdjustViewBounds(true);
            iv.setMaxWidth(dp(200)); iv.setMaxHeight(dp(200));
            iv.setPadding(dp(2), dp(4), dp(2), dp(0));
            wrap.setOrientation(LinearLayout.VERTICAL);
            wrap.addView(iv);
            scrollBottom();
        } catch (Exception ignored) {}
    }

    String extractPartsText(String c) {
        String s = c != null ? c : "";
        if (!s.startsWith("[")) return s;
        try {
            JSONArray a = new JSONArray(s);
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < a.length(); i++) {
                JSONObject p = a.optJSONObject(i);
                if (p != null && "text".equals(p.optString("type")))
                    sb.append(p.optString("text")).append("\n");
            }
            String t = sb.toString().trim();
            if (t.length() > 0) return t;
            for (int i = 0; i < a.length(); i++) {
                JSONObject p = a.optJSONObject(i);
                if (p != null && "image_url".equals(p.optString("type"))) { return "（图片）"; }
            }
            return "（图片）";
        } catch (Exception e) { return s; }
    }

    void extractPartsImg(String c, LinearLayout wrap) {
        if (c == null || !c.startsWith("[")) return;
        try {
            JSONArray a = new JSONArray(c);
            for (int i = 0; i < a.length(); i++) {
                JSONObject p = a.optJSONObject(i);
                if (p == null || !"image_url".equals(p.optString("type"))) continue;
                JSONObject uobj = p.optJSONObject("image_url");
                if (uobj == null) continue;
                String u = uobj.optString("url", "");
                if (u.startsWith("data:")) addUserImage(wrap, u);
            }
        } catch (Exception ignored) {}
    }

    void setApprovalActionsEnabled(View view, boolean enabled) {
        if (view == null) return;
        if ("approval-action".equals(view.getTag()) && view instanceof Button) ((Button) view).setEnabled(enabled);
        if (view instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++)
            setApprovalActionsEnabled(((ViewGroup) view).getChildAt(i), enabled);
    }

    void addApprovalCard(LinearLayout bubble, String id, String tool, String args, String output) {
        LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(roundRect(Color.parseColor("#332815"), dp(11))); card.setPadding(dp(12), dp(9), dp(12), dp(9));
        card.addView(mkText("需要你批准此操作", 13, Color.parseColor("#fbbf24")));
        TextView detail = mkText("工具：" + (tool == null ? "未知" : tool) + "\n参数：", 13, TEXT); card.addView(detail);
        TextView argumentView = mkText(args == null ? "" : args, 12, Color.parseColor("#cbd5e1"));
        argumentView.setTypeface(Typeface.MONOSPACE); argumentView.setTextIsSelectable(true);
        argumentView.setBackground(roundRect(Color.parseColor("#0d1120"), dp(7))); argumentView.setPadding(dp(8), dp(7), dp(8), dp(7)); card.addView(argumentView);
        TextView explanation = mkText(output == null ? "" : output, 12, DIM); explanation.setTextIsSelectable(true); card.addView(explanation);
        LinearLayout actions = new LinearLayout(this); actions.setOrientation(LinearLayout.HORIZONTAL);
        Button yes = smallBtn("批准"); yes.setTextColor(Color.WHITE); yes.setBackground(roundRect(Color.parseColor("#15803d"), dp(8)));
        Button no = smallBtn("拒绝"); no.setBackground(roundRect(Color.parseColor("#5b2330"), dp(8)));
        yes.setTag("approval-action"); no.setTag("approval-action");
        yes.setEnabled(!busy); no.setEnabled(!busy);
        yes.setOnClickListener(v -> { if (busy) { toast("请等当前任务停止后再批准"); return; } yes.setEnabled(false); no.setEnabled(false); postApproval(id, true); });
        no.setOnClickListener(v -> { if (busy) { toast("请等当前任务停止后再批准"); return; } yes.setEnabled(false); no.setEnabled(false); postApproval(id, false); });
        actions.addView(yes, new LinearLayout.LayoutParams(0, dp(40), 1f));
        actions.addView(no, new LinearLayout.LayoutParams(0, dp(40), 1f));
        card.addView(actions);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.topMargin = dp(8); bubble.addView(card, lp);
    }

    JSONObject makeStreamEvent(String type, String message, boolean retryable) {
        JSONObject event = new JSONObject();
        try { event.put("type", type); event.put("message", message); event.put("retryable", retryable); } catch (Exception ignored) {}
        return event;
    }

    void handleEvent(String payload, StreamCtx s) {
        try { enqueueStreamUiEvent(s, new JSONObject(payload)); } catch (Exception ignored) {}
    }

    void enqueueStreamUiEvent(StreamCtx s, JSONObject json) {
        if (s == null || json == null) return;
        String type = json.optString("type", "");
        synchronized (s.displayLock) {
            if (s.displayFinished) return;
            if ("text".equals(type) || "reasoning".equals(type)) {
                String delta = json.optString("delta", "");
                if (delta.isEmpty()) return;
                if ("text".equals(type)) s.receivedText.append(delta); else s.receivedReasoning.append(delta);
                StreamDisplayEvent tail = s.displayQueue.peekLast();
                if (tail != null && ("text".equals(tail.type) || "reasoning".equals(tail.type)) && !type.equals(tail.type)) tail.sealed = true;
                if (tail != null && type.equals(tail.type)) tail.text.append(delta);
                else {
                    StreamDisplayEvent item = new StreamDisplayEvent(); item.type = type; item.text = new StringBuilder(delta);
                    s.displayQueue.addLast(item);
                }
            } else {
                StreamDisplayEvent tail = s.displayQueue.peekLast();
                if (tail != null && ("text".equals(tail.type) || "reasoning".equals(tail.type))) tail.sealed = true;
                StreamDisplayEvent item = new StreamDisplayEvent(); item.type = type; item.json = json;
                s.displayQueue.addLast(item);
            }
        }
        scheduleStreamDisplay(s, 50);
    }

    boolean hasPendingStreamDisplay(StreamCtx s) {
        synchronized (s.displayLock) { return !s.displayQueue.isEmpty(); }
    }

    void scheduleStreamDisplay(StreamCtx s, long delayMs) {
        synchronized (s.displayLock) {
            if (s.drainScheduled || s.displayFinished) return;
            s.drainScheduled = true;
        }
        mainHandler.postDelayed(() -> drainStreamDisplay(s), delayMs);
    }

    void showStreamDrainNotice(StreamCtx s) {
        if (!isCurrentStream(s) || s.displayFinished) return;
        if (s.drainFooter == null) {
            s.drainFooter = new LinearLayout(this); s.drainFooter.setOrientation(LinearLayout.HORIZONTAL);
            s.drainFooter.setGravity(Gravity.CENTER_VERTICAL); s.drainFooter.setPadding(dp(8), dp(3), dp(4), dp(3));
            s.drainFooter.setBackground(roundRect(CARD, dp(9)));
            s.drainStatus = mkText("生成完成，正在显示…", 11, DIM);
            s.drainSkip = smallBtn("显示全部");
            s.drainSkip.setOnClickListener(v -> { s.fastForward = true; scheduleStreamDisplay(s, 0); });
            s.drainFooter.addView(s.drainStatus, new LinearLayout.LayoutParams(0, dp(36), 1f));
            s.drainFooter.addView(s.drainSkip, new LinearLayout.LayoutParams(-2, dp(36)));
        }
        if (s.drainFooter.getParent() == null) s.bubble.addView(s.drainFooter);
        else keepStreamDrainFooterLast(s);
    }

    void keepStreamDrainFooterLast(StreamCtx s) {
        if (s.drainFooter != null && s.drainFooter.getParent() == s.bubble) {
            s.bubble.removeView(s.drainFooter); s.bubble.addView(s.drainFooter);
        }
    }

    void closeDisplayText(StreamCtx s) {
        if (s.curText != null && s.curBuf.length() > 0) { setMd(s.curText, s.curBuf.toString()); s.markdownRenders++; }
        s.curText = null; s.curBuf = new StringBuilder();
    }

    void applyStreamText(StreamCtx s, String delta, long nowMs) {
        dropPh(s); closeReasoningSeg(s);
        if (s.curText == null) { s.curText = addMdText(s.bubble, ""); s.curBuf = new StringBuilder(); }
        s.curBuf.append(delta);
        if (nowMs - s.lastMarkdownMs >= 150) {
            setMd(s.curText, s.curBuf.toString()); s.lastMarkdownMs = nowMs; s.markdownRenders++;
        }
    }

    void applyStreamReasoning(StreamCtx s, String delta, long nowMs) {
        dropPh(s); closeDisplayText(s);
        if (s.curR == null) s.curR = addReasoning(s.bubble, true);
        s.curR.buf.append(delta);
        if (nowMs - s.lastMarkdownMs >= 150) { s.curR.refresh(true); s.lastMarkdownMs = nowMs; }
    }

    void applyStreamStructuralEvent(StreamCtx s, JSONObject ev) {
        String type = ev.optString("type", "");
        switch (type) {
            case "conv":
                if (s.convId.equals(ev.optString("id", s.convId))) curConv = s.convId;
                break;
            case "context":
                contextEstimate = ev.optInt("estimated_input_tokens", 0); contextBudget = ev.optInt("budget", 0);
                break;
            case "approval":
                addApprovalCard(s.bubble, ev.optString("approval_id", ""), ev.optString("tool", "未知"),
                        ev.optString("args", ""), ev.optString("output", ""));
                break;
            case "view_image": {
                LinearLayout chip = new LinearLayout(this); chip.setPadding(dp(8), dp(5), dp(8), dp(5));
                chip.addView(mkText("📷 agent 正在查看图片：" + ev.optString("path", ""), 12, DIM)); s.bubble.addView(chip); break;
            }
            case "tool_start": {
                dropPh(s); closeReasoningSeg(s); closeDisplayText(s);
                String id = ev.optString("id", ""); LinearLayout card = addToolCard(s.bubble, ev.optString("name", "?"), ev.optString("args", "{}"));
                if (id.isEmpty()) card.setTag("running"); else s.cards.put(id, card);
                break;
            }
            case "tool_end": {
                String id = ev.optString("id", ""); LinearLayout card = id.isEmpty() ? null : s.cards.remove(id);
                if (card == null) for (int i = s.bubble.getChildCount() - 1; i >= 0; i--) {
                    View v = s.bubble.getChildAt(i);
                    if (v instanceof LinearLayout && "running".equals(v.getTag())) { ((ViewGroup) v).setTag(null); card = (LinearLayout) v; break; }
                }
                if (card != null) finishToolCard(card, ev.optBoolean("ok", false), ev.optString("summary", ""));
                break;
            }
            case "display_error": {
                dropPh(s); closeReasoningSeg(s); closeDisplayText(s);
                String message = ev.optString("message", "请求失败");
                if ("已停止".equals(message)) { addError(s.bubble, message); s.failed = true; }
                else showFailure(s, message, ev.optBoolean("retryable", true), null, null);
                break;
            }
            case "done": {
                dropPh(s); closeReasoningSeg(s); closeDisplayText(s);
                String err = ev.isNull("error") ? null : ev.optString("error", "");
                if (err != null && !err.isEmpty()) { s.failed = true; showFailure(s, err, ev.optBoolean("retryable", true), ev.optString("status", ""), ev.optJSONObject("usage")); }
                else if (ev.optJSONObject("usage") != null) s.bubble.addView(mkText("用量：" + usageSummary(ev.optJSONObject("usage")), 11, DIM));
                break;
            }
            case "transport_finish": {
                dropPh(s); closeReasoningSeg(s); closeDisplayText(s);
                boolean complete = ev.optBoolean("completed", false), cancelled = ev.optBoolean("cancelled", false);
                if (!complete && !cancelled && !s.failed && lastFailureBubble != s.bubble)
                    showFailure(s, "连接提前结束，服务没有返回完成标记。", true, "interrupted", null);
                if (complete && !s.failed && !cancelled) {
                    if (s.originalDraft != null && input.getText().toString().equals(s.originalDraft)) input.setText("");
                    if (s.originalImage != null && s.originalImage.equals(pendingImg)) pendingImg = null;
                    saveDraft();
                }
                s.displayFinished = true;
                if (s.drainFooter != null) { s.drainStatus.setText("显示完成"); s.drainSkip.setVisibility(View.GONE); }
                break;
            }
            default: break;
        }
        keepStreamDrainFooterLast(s);
    }

    void drainStreamDisplay(StreamCtx s) {
        synchronized (s.displayLock) { s.drainScheduled = false; }
        if (!isCurrentStream(s) || isFinishing()) {
            synchronized (s.displayLock) { s.displayQueue.clear(); s.displayFinished = true; }
            if (s.drainFooter != null && s.drainFooter.getParent() == s.bubble) s.bubble.removeView(s.drainFooter);
            return;
        }
        long nowNanos = System.nanoTime(), nowMs = nowNanos / 1000000L;
        double elapsedMs = Math.max(0, (nowNanos - s.lastDrainNanos) / 1000000.0); s.lastDrainNanos = nowNanos;
        if (s.fastForward) s.charCredit = Integer.MAX_VALUE;
        else s.charCredit = Math.min(30.0, s.charCredit + elapsedMs * 120.0 / 1000.0);
        int allowance = s.fastForward ? Integer.MAX_VALUE : (int) Math.floor(s.charCredit), structural = 0;
        boolean changed = false;
        while (allowance > 0 || structural < 32) {
            StreamDisplayEvent item; String delta = null; JSONObject event = null; String type;
            synchronized (s.displayLock) {
                item = s.displayQueue.peekFirst(); if (item == null) break;
                type = item.type;
                if ("text".equals(type) || "reasoning".equals(type)) {
                    int start = item.offset, end = start, count = 0;
                    while (end < item.text.length() && count < allowance) {
                        char ch = item.text.charAt(end);
                        if (Character.isHighSurrogate(ch) && end + 1 >= item.text.length() && !item.sealed) break;
                        int cp = Character.codePointAt(item.text, end); end += Character.charCount(cp); count++;
                    }
                    if (count > 0) { delta = item.text.substring(start, end); item.offset = end; allowance -= count; }
                    boolean consumed = item.offset >= item.text.length() && item.sealed;
                    if (consumed) s.displayQueue.removeFirst();
                    if (count == 0 || !consumed) {
                        if (delta == null) break;
                        // Apply this final released chunk now; the next tick resumes the same segment.
                    }
                    if (delta == null) continue;
                } else {
                    if (structural >= 32) break;
                    event = item.json; s.displayQueue.removeFirst(); structural++;
                }
            }
            if (delta != null) {
                if ("text".equals(type)) applyStreamText(s, delta, nowMs); else applyStreamReasoning(s, delta, nowMs);
                if (!s.fastForward) s.charCredit = Math.max(0, s.charCredit - delta.codePointCount(0, delta.length()));
            } else applyStreamStructuralEvent(s, event);
            changed = true;
            if (s.displayFinished) break;
            if (allowance <= 0 && structural >= 32) break;
        }
        if (changed) scrollBottom();
        if (s.drainFooter != null) keepStreamDrainFooterLast(s);
        boolean pending;
        synchronized (s.displayLock) { pending = !s.displayQueue.isEmpty(); }
        if (pending && !s.displayFinished) scheduleStreamDisplay(s, 50);
    }

    // ---------------- 极简 Markdown 渲染 ----------------

    static class TableRender {
        String text;
        int headerLines;
    }

    ArrayList<String> tableCells(String line) {
        String s = line == null ? "" : line.trim();
        if (s.startsWith("|")) s = s.substring(1);
        if (s.endsWith("|")) s = s.substring(0, s.length() - 1);
        ArrayList<String> cells = new ArrayList<String>(); StringBuilder cell = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (ch == '\\' && i + 1 < s.length() && (s.charAt(i + 1) == '|' || s.charAt(i + 1) == '\\'))
                cell.append(s.charAt(++i));
            else if (ch == '|') { cells.add(cell.toString().trim()); cell.setLength(0); }
            else cell.append(ch);
        }
        cells.add(cell.toString().trim());
        return cells;
    }

    boolean isPipedTableRow(String line) {
        if (line == null) return false;
        String s = line.trim(); return s.contains("|") && (s.startsWith("|") || s.endsWith("|"));
    }

    boolean isGfmSeparator(String header, String separator) {
        if (!isPipedTableRow(header) || !isPipedTableRow(separator)) return false;
        ArrayList<String> h = tableCells(header), s = tableCells(separator);
        if (h.size() < 2 || h.size() != s.size()) return false;
        for (String cell : s) if (!cell.matches(":?-{3,}:?")) return false;
        return true;
    }

    int tableDisplayWidth(String value) {
        int width = 0;
        for (int i = 0; i < value.length();) {
            int cp = value.codePointAt(i); i += Character.charCount(cp);
            Character.UnicodeScript script = Character.UnicodeScript.of(cp);
            if (script == Character.UnicodeScript.HAN || script == Character.UnicodeScript.HIRAGANA
                    || script == Character.UnicodeScript.KATAKANA || script == Character.UnicodeScript.HANGUL
                    || cp >= 0x1F300) width += 2;
            else width++;
        }
        return width;
    }

    ArrayList<String> wrapTableCell(String value, int width) {
        ArrayList<String> lines = new ArrayList<String>(); StringBuilder line = new StringBuilder(); int used = 0;
        for (int i = 0; i < value.length();) {
            int cp = value.codePointAt(i); String part = new String(Character.toChars(cp)); i += Character.charCount(cp);
            int w = tableDisplayWidth(part);
            if (used > 0 && used + w > width) { lines.add(line.toString()); line.setLength(0); used = 0; }
            line.append(part); used += w;
        }
        if (line.length() > 0 || lines.isEmpty()) lines.add(line.toString());
        return lines;
    }

    String padTableCell(String value, int width) {
        StringBuilder out = new StringBuilder(value);
        for (int i = tableDisplayWidth(value); i < width; i++) out.append(' ');
        return out.toString();
    }

    TableRender renderGfmTable(ArrayList<String> header, ArrayList<ArrayList<String>> rows) {
        int columns = header.size(); int[] widths = new int[columns];
        for (int c = 0; c < columns; c++) widths[c] = Math.max(3, tableDisplayWidth(header.get(c)));
        ArrayList<ArrayList<String>> allRows = new ArrayList<ArrayList<String>>(); allRows.add(header); allRows.addAll(rows);
        for (ArrayList<String> row : allRows) for (int c = 0; c < columns; c++)
            if (c < row.size()) widths[c] = Math.max(widths[c], tableDisplayWidth(row.get(c)));
        // Keep the rendered table near the width of a phone bubble. Long cells wrap
        // inside their column, while every row uses the same compact column widths.
        int maxLineWidth = 40;
        int cellBudget = Math.max(columns, maxLineWidth - 4 * columns);
        int minColumnWidth = cellBudget >= 3 * columns ? 3 : 1;
        for (int c = 0; c < columns; c++) widths[c] = Math.max(minColumnWidth, Math.min(widths[c], 18));
        int totalWidth = 4 * columns;
        for (int width : widths) totalWidth += width;
        while (totalWidth > maxLineWidth) {
            int widest = -1;
            for (int c = 0; c < columns; c++)
                if (widths[c] > minColumnWidth && (widest < 0 || widths[c] > widths[widest])) widest = c;
            if (widest < 0) break;
            widths[widest]--;
            totalWidth--;
        }
        StringBuilder out = new StringBuilder(); int headerLines = 0;
        for (int r = 0; r < allRows.size(); r++) {
            ArrayList<String> row = allRows.get(r); ArrayList<ArrayList<String>> wrapped = new ArrayList<ArrayList<String>>(); int height = 1;
            for (int c = 0; c < columns; c++) {
                String cell = c < row.size() ? row.get(c) : "";
                ArrayList<String> pieces = wrapTableCell(cell, widths[c]); wrapped.add(pieces); height = Math.max(height, pieces.size());
            }
            if (r == 0) headerLines = height;
            for (int line = 0; line < height; line++) {
                for (int c = 0; c < columns; c++) {
                    String piece = line < wrapped.get(c).size() ? wrapped.get(c).get(line) : "";
                    out.append("| ").append(padTableCell(piece, widths[c])).append(' ');
                }
                out.append('|');
                if (r + 1 < allRows.size() || line + 1 < height) out.append('\n');
            }
        }
        TableRender result = new TableRender(); result.text = out.toString(); result.headerLines = headerLines; return result;
    }

    boolean mdProtected(ArrayList<int[]> ranges, int start, int end) {
        for (int[] range : ranges) if (start < range[1] && end > range[0]) return true;
        return false;
    }

    void mdAdjustRanges(ArrayList<int[]> ranges, int start, int end, int insertedLength) {
        int delta = insertedLength - (end - start);
        if (delta == 0) return;
        for (int[] range : ranges) {
            if (range[0] >= end) { range[0] += delta; range[1] += delta; }
            else if (range[1] > start) range[1] = Math.max(range[0], range[1] + delta);
        }
    }

    void applyInlineMarkdown(SpannableStringBuilder text, ArrayList<int[]> protectedRanges) {
        String source = text.toString();
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("`([^`\\n]+)`").matcher(source);
        ArrayList<int[]> inlineCode = new ArrayList<int[]>();
        while (m.find()) if (!mdProtected(protectedRanges, m.start(), m.end())) inlineCode.add(new int[]{m.start(), m.end()});
        for (int i = inlineCode.size() - 1; i >= 0; i--) {
            int start = inlineCode.get(i)[0], end = inlineCode.get(i)[1];
            int contentStart = start + 1, contentEnd = end - 1;
            text.setSpan(new BackgroundColorSpan(Color.parseColor("#0d1120")), contentStart, contentEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            text.setSpan(new android.text.style.TypefaceSpan("monospace"), contentStart, contentEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            mdAdjustRanges(protectedRanges, end - 1, end, 0);
            text.delete(end - 1, end); text.delete(start, start + 1);
            mdAdjustRanges(protectedRanges, start, start + 1, 0);
            protectedRanges.add(new int[]{start, end - 2});
        }

        source = text.toString();
        m = java.util.regex.Pattern.compile("\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)").matcher(source);
        ArrayList<int[]> links = new ArrayList<int[]>();
        ArrayList<String[]> linkValues = new ArrayList<String[]>();
        while (m.find()) if (!mdProtected(protectedRanges, m.start(), m.end())) {
            links.add(new int[]{m.start(), m.end()}); linkValues.add(new String[]{m.group(1), m.group(2)});
        }
        for (int i = links.size() - 1; i >= 0; i--) {
            int start = links.get(i)[0], end = links.get(i)[1]; String label = linkValues.get(i)[0], url = linkValues.get(i)[1];
            mdAdjustRanges(protectedRanges, start, end, label.length());
            text.replace(start, end, label);
            if (url.matches("(?i)^https?://[^\\s<>]+$"))
                text.setSpan(new URLSpan(url), start, start + label.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }

        String[][] patterns = {
                {"\\*\\*([^*\\n]+)\\*\\*", "bold"},
                {"~~([^~\\n]+)~~", "strike"},
                {"(?<!\\*)\\*([^*\\n]+)\\*(?!\\*)", "italic"}
        };
        for (String[] rule : patterns) {
            source = text.toString(); m = java.util.regex.Pattern.compile(rule[0]).matcher(source);
            ArrayList<int[]> matches = new ArrayList<int[]>();
            while (m.find()) if (!mdProtected(protectedRanges, m.start(), m.end())) matches.add(new int[]{m.start(), m.end()});
            for (int i = matches.size() - 1; i >= 0; i--) {
                int start = matches.get(i)[0], end = matches.get(i)[1];
                int marker = "bold".equals(rule[1]) || "strike".equals(rule[1]) ? 2 : 1;
                int contentStart = start + marker, contentEnd = end - marker;
                Object span = "bold".equals(rule[1]) ? new StyleSpan(Typeface.BOLD)
                        : "strike".equals(rule[1]) ? new android.text.style.StrikethroughSpan()
                        : new StyleSpan(Typeface.ITALIC);
                text.setSpan(span, contentStart, contentEnd, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                mdAdjustRanges(protectedRanges, end - marker, end, 0);
                text.delete(end - marker, end); text.delete(start, start + marker);
                mdAdjustRanges(protectedRanges, start, start + marker, 0);
            }
        }
    }

    void setMd(TextView tv, String src) {
        if (src == null || src.isEmpty()) { tv.setText(""); return; }
        String[] lines = src.replace("\r", "").split("\n", -1);
        StringBuilder rendered = new StringBuilder();
        ArrayList<int[]> headings = new ArrayList<int[]>();
        ArrayList<int[]> codeLines = new ArrayList<int[]>();
        ArrayList<int[]> tableLines = new ArrayList<int[]>();
        ArrayList<int[]> tableHeaders = new ArrayList<int[]>();
        ArrayList<int[]> quotes = new ArrayList<int[]>();
        boolean inFence = false;
        for (int lineIndex = 0; lineIndex < lines.length;) {
            String raw = lines[lineIndex];
            if (!inFence && lineIndex + 1 < lines.length && isGfmSeparator(raw, lines[lineIndex + 1])) {
                ArrayList<String> header = tableCells(raw); ArrayList<ArrayList<String>> rows = new ArrayList<ArrayList<String>>();
                int end = lineIndex + 2;
                while (end < lines.length && isPipedTableRow(lines[end])) rows.add(tableCells(lines[end++]));
                TableRender table = renderGfmTable(header, rows); int start = rendered.length();
                rendered.append(table.text);
                String[] outputRows = table.text.split("\n", -1); int offset = start;
                for (int i = 0; i < outputRows.length; i++) {
                    int lineEnd = offset + outputRows[i].length(); tableLines.add(new int[]{offset, lineEnd});
                    if (i < table.headerLines) tableHeaders.add(new int[]{offset, lineEnd});
                    offset = lineEnd + 1;
                }
                rendered.append('\n'); lineIndex = end; continue;
            }
            String line = raw;
            if (line.trim().startsWith("```")) { inFence = !inFence; lineIndex++; continue; }
            int start = rendered.length();
            if (!inFence) {
                java.util.regex.Matcher hm = java.util.regex.Pattern.compile("^\\s{0,3}(#{1,6})\\s+(.+?)\\s*#*\\s*$").matcher(line);
                if (hm.matches()) {
                    line = hm.group(2);
                    headings.add(new int[]{start, start + line.length(), hm.group(1).length()});
                } else {
                    java.util.regex.Matcher qm = java.util.regex.Pattern.compile("^\\s{0,3}(>\\s?)+(.+)$").matcher(line);
                    if (qm.matches()) {
                        line = qm.group(2);
                        quotes.add(new int[]{start, start + line.length()});
                    } else {
                        java.util.regex.Matcher lm = java.util.regex.Pattern.compile("^(\\s*)([-*+]\\s+|\\d+[.)]\\s+)(?:\\[([ xX])\\]\\s+)?(.*)$").matcher(line);
                        if (lm.matches()) {
                            String task = lm.group(3);
                            line = lm.group(1) + "• " + (task == null ? "" : ("xX".contains(task) ? "☑ " : "☐ ")) + lm.group(4);
                        }
                    }
                }
            } else {
                codeLines.add(new int[]{start, start + line.length()});
            }
            rendered.append(line).append('\n');
            lineIndex++;
        }
        if (rendered.length() > 0) rendered.setLength(rendered.length() - 1);
        String text = rendered.toString();
        SpannableStringBuilder sp = new SpannableStringBuilder(rendered);
        for (int[] h : headings) {
            if (h[0] < h[1] && h[1] <= text.length()) {
                sp.setSpan(new RelativeSizeSpan(h[2] <= 2 ? 1.3f : 1.12f), h[0], h[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                sp.setSpan(new StyleSpan(Typeface.BOLD), h[0], h[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
        }
        for (int[] range : codeLines) if (range[0] < range[1] && range[1] <= text.length()) {
            sp.setSpan(new BackgroundColorSpan(Color.parseColor("#0d1120")), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            sp.setSpan(new ForegroundColorSpan(Color.parseColor("#c4b5fd")), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            sp.setSpan(new android.text.style.TypefaceSpan("monospace"), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        for (int[] range : tableLines) if (range[0] < range[1] && range[1] <= text.length())
        {
            sp.setSpan(new android.text.style.TypefaceSpan("monospace"), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            sp.setSpan(new android.text.style.RelativeSizeSpan(0.84f), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        for (int[] range : tableHeaders) if (range[0] < range[1] && range[1] <= text.length())
            sp.setSpan(new StyleSpan(Typeface.BOLD), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        for (int[] range : quotes) if (range[0] < range[1] && range[1] <= sp.length()) {
            sp.setSpan(new ForegroundColorSpan(Color.parseColor("#a7b5cf")), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            sp.setSpan(new android.text.style.QuoteSpan(Color.parseColor("#4f8cff")), range[0], range[1], Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        ArrayList<int[]> protectedRanges = new ArrayList<int[]>();
        protectedRanges.addAll(codeLines); protectedRanges.addAll(tableLines);
        applyInlineMarkdown(sp, protectedRanges);
        tv.setText(sp);
        tv.setMovementMethod(LinkMovementMethod.getInstance());
        tv.setLinksClickable(true);
    }
}
