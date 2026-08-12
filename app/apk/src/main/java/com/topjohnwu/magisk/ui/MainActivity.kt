package com.topjohnwu.magisk.ui

import android.Manifest
import android.Manifest.permission.REQUEST_INSTALL_PACKAGES
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.view.forEach
import androidx.core.view.isGone
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import androidx.navigation.NavDirections
import com.topjohnwu.magisk.MainDirections
import com.topjohnwu.magisk.R
import com.topjohnwu.magisk.arch.BaseViewModel
import com.topjohnwu.magisk.arch.NavigationActivity
import com.topjohnwu.magisk.arch.startAnimations
import com.topjohnwu.magisk.arch.viewModel
import com.topjohnwu.magisk.core.Config
import com.topjohnwu.magisk.core.Const
import com.topjohnwu.magisk.core.Info
import com.topjohnwu.magisk.core.base.SplashController
import com.topjohnwu.magisk.core.base.SplashScreenHost
import com.topjohnwu.magisk.core.isRunningAsStub
import com.topjohnwu.magisk.core.ktx.toast
import com.topjohnwu.magisk.core.model.module.LocalModule
import com.topjohnwu.magisk.core.tasks.AppMigration
import com.topjohnwu.magisk.databinding.ActivityMainMd2Binding
import com.topjohnwu.magisk.ui.home.HomeFragmentDirections
import com.topjohnwu.magisk.ui.theme.Theme
import com.topjohnwu.magisk.view.MagiskDialog
import com.topjohnwu.magisk.view.Shortcuts
import kotlinx.coroutines.launch
import java.io.File
import com.topjohnwu.magisk.core.R as CoreR

import rikka.shizuku.Shizuku


class MainViewModel : BaseViewModel()


class MainActivity :
    NavigationActivity<ActivityMainMd2Binding>(),
    SplashScreenHost {

    override val layoutRes = R.layout.activity_main_md2

    override val viewModel by viewModel<MainViewModel>()

    override val navHostId: Int = R.id.main_nav_host

    override val splashController = SplashController(this)

    override val snackbarView: View
        get() {
            val fragmentOverride = currentFragment?.snackbarView
            return fragmentOverride ?: super.snackbarView
        }

    override val snackbarAnchorView: View?
        get() {
            val fragmentAnchor = currentFragment?.snackbarAnchorView

            return when {
                fragmentAnchor?.isVisible == true -> fragmentAnchor

                binding.mainNavigation.isVisible ->
                    binding.mainNavigation

                else -> null
            }
        }

    private var isRootFragment = true


    /*
     * ============================================================
     * SHIZUKU
     * ============================================================
     */

    private companion object {

        const val SHIZUKU_PERMISSION_REQUEST = 1000
    }


    /**
     * Listener chamado depois que o usuário responde
     * à solicitação de permissão do Shizuku.
     */
    private val shizukuPermissionListener =
        Shizuku.OnRequestPermissionResultListener { requestCode, grantResult ->

            if (requestCode != SHIZUKU_PERMISSION_REQUEST) {
                return@OnRequestPermissionResultListener
            }

            if (grantResult == PackageManager.PERMISSION_GRANTED) {

                Toast.makeText(
                    this,
                    "Permissão Shizuku concedida",
                    Toast.LENGTH_SHORT
                ).show()

                showShizukuStatus()

            } else {

                Toast.makeText(
                    this,
                    "Permissão Shizuku negada",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }


    /**
     * Verifica se o serviço Shizuku está disponível.
     */
    private fun isShizukuAvailable(): Boolean {

        return try {

            Shizuku.pingBinder()

        } catch (_: Throwable) {

            false
        }
    }


    /**
     * Verifica se o aplicativo possui a permissão Shizuku.
     */
    private fun hasShizukuPermission(): Boolean {

        return try {

            Shizuku.checkSelfPermission() ==
                    PackageManager.PERMISSION_GRANTED

        } catch (_: Throwable) {

            false
        }
    }


    /**
     * Retorna o UID utilizado pelo Shizuku.
     *
     * UID 0    = root
     * UID 2000  = shell/ADB
     */
    private fun getShizukuUid(): Int? {

        return try {

            if (!Shizuku.pingBinder()) {
                null
            } else {
                Shizuku.getUid()
            }

        } catch (_: Throwable) {

            null
        }
    }


    /**
     * Retorna uma descrição do estado do Shizuku.
     */
    private fun getShizukuStatus(): String {

        if (!isShizukuAvailable()) {
            return "Shizuku não está ativo"
        }

        if (!hasShizukuPermission()) {
            return "Shizuku ativo, mas sem permissão"
        }

        return when (getShizukuUid()) {

            0 -> "Shizuku ativo — Root"

            2000 -> "Shizuku ativo — ADB/Shell"

            null -> "Shizuku indisponível"

            else ->
                "Shizuku ativo — UID ${getShizukuUid()}"
        }
    }


    /**
     * Solicita a autorização do Shizuku.
     */
    private fun requestShizukuPermission() {

        if (!isShizukuAvailable()) {

            Toast.makeText(
                this,
                "Inicie o Shizuku primeiro",
                Toast.LENGTH_LONG
            ).show()

            return
        }


        if (hasShizukuPermission()) {

            showShizukuStatus()

            return
        }


        try {

            Shizuku.requestPermission(
                SHIZUKU_PERMISSION_REQUEST
            )

        } catch (e: Throwable) {

            Toast.makeText(
                this,
                "Erro ao solicitar Shizuku: ${e.message}",
                Toast.LENGTH_LONG
            ).show()
        }
    }


    /**
     * Mostra o estado atual do Shizuku.
     */
    private fun showShizukuStatus() {

        val status = getShizukuStatus()

        MagiskDialog(this).apply {

            setTitle("Shizuku")

            setMessage(status)

            if (isShizukuAvailable() &&
                !hasShizukuPermission()
            ) {

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = "Autorizar"

                    onClick {

                        requestShizukuPermission()
                    }
                }

            } else {

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok
                }
            }

            setCancelable(true)

        }.show()
    }


    /**
     * Inicializa a integração com Shizuku.
     */
    private fun initializeShizuku() {

        try {

            Shizuku.addRequestPermissionResultListener(
                shizukuPermissionListener
            )

        } catch (_: Throwable) {
            // Shizuku não instalado ou API incompatível.
        }
    }


    /**
     * Remove o listener do Shizuku.
     */
    private fun destroyShizuku() {

        try {

            Shizuku.removeRequestPermissionResultListener(
                shizukuPermissionListener
            )

        } catch (_: Throwable) {
        }
    }


    /*
     * ============================================================
     * ACTIVITY
     * ============================================================
     */

    override fun onCreate(
        savedInstanceState: Bundle?
    ) {

        setTheme(Theme.selected.themeRes)

        splashController.preOnCreate()

        super.onCreate(savedInstanceState)

        splashController.onCreate(savedInstanceState)

        initializeShizuku()
    }


    override fun onResume() {

        super.onResume()

        splashController.onResume()
    }


    override fun onDestroy() {

        destroyShizuku()

        super.onDestroy()
    }


    @SuppressLint("InlinedApi")
    override fun onCreateUi(
        savedInstanceState: Bundle?
    ) {

        setContentView()

        showUnsupportedMessage()

        askForHomeShortcut()


        /*
         * ========================================================
         * NOTIFICAÇÕES
         * ========================================================
         */

        if (Config.checkUpdate) {

            withPermission(
                Manifest.permission.POST_NOTIFICATIONS
            ) {

                Config.checkUpdate = it
            }
        }


        window.setSoftInputMode(
            WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        )


        /*
         * ========================================================
         * NAVEGAÇÃO
         * ========================================================
         */

        navigation.addOnDestinationChangedListener {
                _,
                destination,
                _ ->

            isRootFragment = when (destination.id) {

                R.id.homeFragment,
                R.id.modulesFragment,
                R.id.superuserFragment,
                R.id.logFragment -> true

                else -> false
            }


            setDisplayHomeAsUpEnabled(
                !isRootFragment
            )

            requestNavigationHidden(
                !isRootFragment
            )


            binding.mainNavigation.menu.forEach {

                if (it.itemId == destination.id) {

                    it.isChecked = true
                }
            }
        }


        setSupportActionBar(
            binding.mainToolbar
        )


        binding.mainNavigation.setOnItemSelectedListener {

            getScreen(it.itemId)?.navigate()

            true
        }


        binding.mainNavigation.setOnItemReselectedListener {

            // https://issuetracker.google.com/issues/124538620
        }


        binding.mainNavigation.menu.apply {

            findItem(
                R.id.superuserFragment
            )?.isEnabled = Info.showSuperUser


            findItem(
                R.id.modulesFragment
            )?.isEnabled =
                Info.env.isActive &&
                LocalModule.loaded()
        }


        /*
         * ========================================================
         * SEÇÃO INICIAL
         * ========================================================
         */

        val section =
            if (
                intent.action ==
                Intent.ACTION_APPLICATION_PREFERENCES
            ) {

                Const.Nav.SETTINGS

            } else {

                intent.getStringExtra(
                    Const.Key.OPEN_SECTION
                )
            }


        getScreen(section)?.navigate()


        if (!isRootFragment) {

            requestNavigationHidden(
                requiresAnimation =
                    savedInstanceState == null
            )
        }


        /*
         * ========================================================
         * SHIZUKU
         * ========================================================
         *
         * Aqui apenas verificamos se o serviço está disponível.
         *
         * Não solicitamos automaticamente a permissão para
         * não abrir uma janela do Shizuku toda vez que o Magisk
         * iniciar.
         */

        checkShizukuSilently()
    }


    /**
     * Verificação silenciosa do Shizuku.
     */
    private fun checkShizukuSilently() {

        try {

            if (Shizuku.pingBinder()) {

                // Shizuku está disponível.
                //
                // A interface pode utilizar:
                //
                // getShizukuStatus()
                //
                // para descobrir o estado.

            }

        } catch (_: Throwable) {

            // Shizuku não está disponível.
        }
    }


    /*
     * ============================================================
     * TOOLBAR
     * ============================================================
     */

    override fun onOptionsItemSelected(
        item: MenuItem
    ): Boolean {

        when (item.itemId) {

            android.R.id.home -> onBackPressed()

            else ->
                return super.onOptionsItemSelected(item)
        }

        return true
    }


    fun setDisplayHomeAsUpEnabled(
        isEnabled: Boolean
    ) {

        binding.mainToolbar.startAnimations()

        when {

            isEnabled ->
                binding.mainToolbar.setNavigationIcon(
                    R.drawable.ic_back_md2
                )

            else ->
                binding.mainToolbar.navigationIcon = null
        }
    }


    internal fun requestNavigationHidden(
        hide: Boolean = true,
        requiresAnimation: Boolean = true
    ) {

        val bottomView =
            binding.mainNavigation


        if (requiresAnimation) {

            bottomView.isVisible = true

            bottomView.isHidden = hide

        } else {

            bottomView.isGone = hide
        }
    }


    fun invalidateToolbar() {

        // binding.mainToolbar.startAnimations()

        binding.mainToolbar.invalidate()
    }


    /*
     * ============================================================
     * NAVIGATION
     * ============================================================
     */

    private fun getScreen(
        name: String?
    ): NavDirections? {

        return when (name) {

            Const.Nav.SUPERUSER ->
                MainDirections.actionSuperuserFragment()

            Const.Nav.MODULES ->
                MainDirections.actionModuleFragment()

            Const.Nav.SETTINGS ->
                HomeFragmentDirections
                    .actionHomeFragmentToSettingsFragment()

            else -> null
        }
    }


    private fun getScreen(
        id: Int
    ): NavDirections? {

        return when (id) {

            R.id.homeFragment ->
                MainDirections.actionHomeFragment()

            R.id.modulesFragment ->
                MainDirections.actionModuleFragment()

            R.id.superuserFragment ->
                MainDirections.actionSuperuserFragment()

            R.id.logFragment ->
                MainDirections.actionLogFragment()

            else -> null
        }
    }


    /*
     * ============================================================
     * INVALID STATE
     * ============================================================
     */

    @SuppressLint("InlinedApi")
    override fun showInvalidStateMessage(): Unit =
        runOnUiThread {

            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.unsupport_nonroot_stub_title
                )

                setMessage(
                    CoreR.string.unsupport_nonroot_stub_msg
                )


                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = CoreR.string.install


                    onClick {

                        withPermission(
                            REQUEST_INSTALL_PACKAGES
                        ) {

                            if (!it) {

                                toast(
                                    CoreR.string.install_unknown_denied,
                                    Toast.LENGTH_SHORT
                                )

                                showInvalidStateMessage()

                            } else {

                                lifecycleScope.launch {

                                    AppMigration.restore(
                                        this@MainActivity
                                    )
                                }
                            }
                        }
                    }
                }


                setCancelable(false)

                show()
            }
        }


    /*
     * ============================================================
     * UNSUPPORTED MESSAGE
     * ============================================================
     */

    private fun showUnsupportedMessage() {

        if (Info.env.isUnsupported) {

            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.unsupport_magisk_title
                )

                setMessage(
                    CoreR.string.unsupport_magisk_msg,
                    Const.Version.MIN_VERSION
                )

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok
                }

                setCancelable(false)

            }.show()
        }


        if (
            !Info.isEmulator &&
            Info.env.isActive &&
            System.getenv("PATH")
                ?.split(':')
                ?.filterNot {
                    File("$it/magisk").exists()
                }
                ?.any {
                    File("$it/su").exists()
                } == true
        ) {

            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.unsupport_general_title
                )

                setMessage(
                    CoreR.string.unsupport_other_su_msg
                )

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok
                }

                setCancelable(false)

            }.show()
        }


        if (
            applicationInfo.flags and
            ApplicationInfo.FLAG_SYSTEM != 0
        ) {

            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.unsupport_general_title
                )

                setMessage(
                    CoreR.string.unsupport_system_app_msg
                )

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok
                }

                setCancelable(false
                )

            }.show()
        }


        if (
            applicationInfo.flags and
            ApplicationInfo.FLAG_EXTERNAL_STORAGE != 0
        ) {

            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.unsupport_general_title
                )

                setMessage(
                    CoreR.string.unsupport_external_storage_msg
                )

                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok
                }

                setCancelable(true)

            }.show()
        }
    }


    /*
     * ============================================================
     * HOME SHORTCUT
     * ============================================================
     */

    private fun askForHomeShortcut() {

        if (
            isRunningAsStub &&
            !Config.askedHome &&
            ShortcutManagerCompat
                .isRequestPinShortcutSupported(this)
        ) {

            Config.askedHome = true


            MagiskDialog(this).apply {

                setTitle(
                    CoreR.string.add_shortcut_title
                )

                setMessage(
                    CoreR.string.add_shortcut_msg
                )


                setButton(
                    MagiskDialog.ButtonType.NEGATIVE
                ) {

                    text = android.R.string.cancel
                }


                setButton(
                    MagiskDialog.ButtonType.POSITIVE
                ) {

                    text = android.R.string.ok

                    onClick {

                        Shortcuts.addHomeIcon(
                            this@MainActivity
                        )
                    }
                }


                setCancelable(true)

            }.show()
        }
    }
    }
