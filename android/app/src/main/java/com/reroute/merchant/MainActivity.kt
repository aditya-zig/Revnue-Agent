package com.reroute.merchant

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

/**
 * Throwaway WebView shell for the in-app prototype at /prototype/mobile.
 * Keeps prototype inside the app and buildable to APK per request.
 * Branch: prototype/android-merchant-recovery — not production code.
 */
class MainActivity : AppCompatActivity() {

    // Change this to your host IP for a physical device.
    // Emulator maps host loopback to 10.0.2.2.
    private val WEBVIEW_URL = "http://10.0.2.2:8000/prototype/mobile?variant=B"

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val webView = WebView(this)
        setContentView(webView)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.allowFileAccess = false
        // Cleartext http is intentional for local prototype; prod would use https
        webView.webViewClient = WebViewClient()

        // Variant is also switchable inside the page via the floating pill
        // and ?variant=A/B/C URL param (shareable). This shell just hosts it.
        val url = intent.getStringExtra("prototype_url") ?: WEBVIEW_URL
        webView.loadUrl(url)
    }

    override fun onBackPressed() {
        val wv = findViewById<WebView>(android.R.id.content)?.getChildAt(0) as? WebView
        if (wv?.canGoBack() == true) wv.goBack() else super.onBackPressed()
    }
}
