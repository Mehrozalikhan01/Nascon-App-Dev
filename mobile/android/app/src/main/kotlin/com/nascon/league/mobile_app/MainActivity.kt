package com.nascon.league.mobile_app

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class MainActivity : FlutterActivity() {
    private val secureChannel = "nascon.secure_storage"
    private val localChannel = "nascon.local_store"
    private val receiptChannel = "nascon.receipt"
    private lateinit var localStore: LocalStore

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        localStore = LocalStore(applicationContext)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, secureChannel)
            .setMethodCallHandler { call, result ->
                try {
                    when (call.method) {
                        "readToken" -> result.success(readEncryptedToken())
                        "writeToken" -> {
                            val token = call.argument<String>("token")
                            if (token.isNullOrBlank()) result.error("INVALID_TOKEN", "Token is empty", null)
                            else {
                                writeEncryptedToken(token)
                                result.success(null)
                            }
                        }
                        "deleteToken" -> {
                            getSharedPreferences("nascon_secure", Context.MODE_PRIVATE)
                                .edit().remove("auth_token").apply()
                            result.success(null)
                        }
                        else -> result.notImplemented()
                    }
                } catch (error: Exception) {
                    result.error("SECURE_STORAGE_ERROR", "Could not access encrypted token storage", error.message)
                }
            }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, localChannel)
            .setMethodCallHandler { call, result ->
                try {
                    val key = call.argument<String>("key")
                    if (key.isNullOrBlank()) {
                        result.error("INVALID_KEY", "Storage key is required", null)
                    } else {
                        when (call.method) {
                            "get" -> result.success(localStore.get(key))
                            "set" -> {
                                val value = call.argument<String>("value")
                                if (value == null) result.error("INVALID_VALUE", "Storage value is required", null)
                                else {
                                    localStore.put(key, value)
                                    result.success(null)
                                }
                            }
                            "remove" -> {
                                localStore.remove(key)
                                result.success(null)
                            }
                            else -> result.notImplemented()
                        }
                    }
                } catch (error: Exception) {
                    result.error("LOCAL_STORE_ERROR", "Could not access local app storage", error.message)
                }
            }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, receiptChannel)
            .setMethodCallHandler { call, result ->
                if (call.method != "saveAndOpen") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                try {
                    val encodedPdf = call.argument<String>("pdfBase64")
                    val orderId = call.argument<String>("orderId") ?: "receipt"
                    if (encodedPdf.isNullOrBlank()) {
                        result.error("INVALID_RECEIPT", "Receipt PDF is missing", null)
                    } else {
                        val (uri, displayName) = saveReceiptPdf(encodedPdf, orderId)
                        openReceipt(uri)
                        result.success(displayName)
                    }
                } catch (error: Exception) {
                    result.error("RECEIPT_SAVE_ERROR", "Could not save the receipt PDF", error.message)
                }
            }
    }

    private fun readEncryptedToken(): String? {
        val encoded = getSharedPreferences("nascon_secure", Context.MODE_PRIVATE)
            .getString("auth_token", null) ?: return null
        val bytes = Base64.decode(encoded, Base64.NO_WRAP)
        if (bytes.size < 14 || bytes[0].toInt() != 1) return null
        val ivLength = bytes[1].toInt() and 0xff
        if (ivLength < 12 || bytes.size <= 2 + ivLength) return null
        val iv = bytes.copyOfRange(2, 2 + ivLength)
        val ciphertext = bytes.copyOfRange(2 + ivLength, bytes.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(128, iv))
        return String(cipher.doFinal(ciphertext), Charsets.UTF_8)
    }

    private fun writeEncryptedToken(token: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val iv = cipher.iv
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val output = ByteBuffer.allocate(2 + iv.size + encrypted.size)
            .put(1.toByte()).put(iv.size.toByte()).put(iv).put(encrypted).array()
        val encoded = Base64.encodeToString(output, Base64.NO_WRAP)
        getSharedPreferences("nascon_secure", Context.MODE_PRIVATE)
            .edit().putString("auth_token", encoded).apply()
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val alias = "nascon_access_token_v1"
        val existing = keyStore.getKey(alias, null) as? SecretKey
        if (existing != null) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    private fun saveReceiptPdf(encodedPdf: String, orderId: String): Pair<Uri, String> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            throw IllegalStateException("Saving receipts requires Android 10 or later")
        }
        val pdf = Base64.decode(encodedPdf, Base64.DEFAULT)
        if (pdf.size < 8 || !pdf.copyOfRange(0, 5).contentEquals("%PDF-".toByteArray())) {
            throw IllegalArgumentException("The receipt is not a valid PDF")
        }
        val safeOrderId = orderId.filter { it.isLetterOrDigit() || it == '-' || it == '_' || it == '.' }
            .take(64).ifBlank { "receipt" }
        val displayName = "NaSCon-26-$safeOrderId.pdf"
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
            put(MediaStore.MediaColumns.MIME_TYPE, "application/pdf")
            put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/NaSCon")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("Could not create a file in Downloads")
        try {
            contentResolver.openOutputStream(uri)?.use { it.write(pdf) }
                ?: throw IllegalStateException("Could not write the receipt file")
            val completed = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
            contentResolver.update(uri, completed, null, null)
        } catch (error: Exception) {
            contentResolver.delete(uri, null, null)
            throw error
        }
        return uri to displayName
    }

    private fun openReceipt(uri: Uri) {
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/pdf")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        try {
            startActivity(intent)
        } catch (_: android.content.ActivityNotFoundException) {
            // The PDF is still saved in Downloads/NaSCon for a PDF viewer to open.
        }
    }
}

private class LocalStore(context: Context) : SQLiteOpenHelper(context, "nascon_local.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE local_values (storage_key TEXT PRIMARY KEY NOT NULL, storage_value TEXT NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Versioned schema changes will be applied here.
    }

    fun get(key: String): String? = readableDatabase.query(
        "local_values", arrayOf("storage_value"), "storage_key = ?", arrayOf(key), null, null, null
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

    fun put(key: String, value: String) {
        val row = ContentValues().apply {
            put("storage_key", key)
            put("storage_value", value)
        }
        writableDatabase.insertWithOnConflict("local_values", null, row, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun remove(key: String) {
        writableDatabase.delete("local_values", "storage_key = ?", arrayOf(key))
    }
}
