import { createGlobalStyle } from "antd-style";
import {
  ConfigProvider,
  bailianDarkTheme,
  bailianTheme,
} from "@agentscope-ai/design";
import { App as AntdApp, theme as antdTheme } from "antd";
import type { ThemeConfig } from "antd";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import jaJP from "antd/locale/ja_JP";
import ruRU from "antd/locale/ru_RU";
import idID from "antd/locale/id_ID";
import type { Locale } from "antd/es/locale";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/zh-cn";
import "dayjs/locale/ja";
import "dayjs/locale/ru";
import "dayjs/locale/id";
dayjs.extend(relativeTime);
import MainLayout from "./layouts/MainLayout";
import { ThemeProvider, useTheme } from "./contexts/ThemeContext";
import { PluginProvider } from "./plugins/PluginContext";
import { ApprovalProvider } from "./contexts/ApprovalContext";
import { lazyImportWithRetry } from "./utils/lazyWithRetry";
import {
  getLoginHref,
  getLoginPath,
  getRouterBasename,
} from "./utils/navigationMode";

const LoginPage = lazyImportWithRetry("./pages/Login/index");
import { languageApi } from "./api/modules/language";
import { useUploadLimitStore } from "./stores/uploadLimitStore";
import {
  resolveAuthGate,
  resolveBackendInfo,
  type BackendInfo,
} from "./auth/gate";
import type { AuthStatusResponse } from "./api/modules/auth";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/form-override.css";

const antdLocaleMap: Record<string, Locale> = {
  zh: zhCN,
  en: enUS,
  ja: jaJP,
  ru: ruRU,
  id: idID,
};

const dayjsLocaleMap: Record<string, string> = {
  zh: "zh-cn",
  en: "en",
  ja: "ja",
  ru: "ru",
  id: "id",
};

const GlobalStyle = createGlobalStyle`
* {
  margin: 0;
  box-sizing: border-box;
}
`;

function AuthGuard({
  children,
  authStatus,
  useHardRedirect = false,
}: {
  children: React.ReactNode;
  authStatus: AuthStatusResponse;
  useHardRedirect?: boolean;
}) {
  const [status, setStatus] = useState<
    "loading" | "auth-required" | "ok" | "error"
  >("loading");
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setErrorMessage("");
    resolveAuthGate(authStatus)
      .then((nextStatus) => {
        if (!cancelled) setStatus(nextStatus);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setErrorMessage(
          error instanceof Error ? error.message : "Authentication failed",
        );
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [authStatus]);

  if (status === "loading") {
    return null;
  }
  if (status === "error") {
    return <div>Authentication error: {errorMessage}</div>;
  }
  if (status === "auth-required") {
    const loginTo = getLoginPath(window.location);
    if (useHardRedirect) {
      // The OS shell renders outside a Router, so <Navigate> is unavailable.
      window.location.replace(getLoginHref(window.location));
      return null;
    }
    return <Navigate to={loginTo} replace />;
  }
  return <>{children}</>;
}

function AppInner({ backendInfo }: { backendInfo: BackendInfo }) {
  const basename = getRouterBasename(window.location.pathname);
  const { i18n } = useTranslation();
  const { isDark } = useTheme();
  const selectedTheme = isDark ? bailianDarkTheme : bailianTheme;
  const lang = i18n.resolvedLanguage || i18n.language || "en";
  const [antdLocale, setAntdLocale] = useState<Locale>(
    antdLocaleMap[lang] ?? enUS,
  );

  useEffect(() => {
    if (!localStorage.getItem("language")) {
      languageApi
        .getLanguage()
        .then(({ language }) => {
          if (language && language !== i18n.language) {
            i18n.changeLanguage(language);
            localStorage.setItem("language", language);
          }
        })
        .catch((err) =>
          console.error("Failed to fetch language preference:", err),
        );
    }
    useUploadLimitStore.getState().fetch();
  }, []);

  useEffect(() => {
    const handleLanguageChanged = (lng: string) => {
      const shortLng = lng.split("-")[0];
      setAntdLocale(antdLocaleMap[shortLng] ?? enUS);
      dayjs.locale(dayjsLocaleMap[shortLng] ?? "en");
    };

    // Set initial dayjs locale
    dayjs.locale(dayjsLocaleMap[lang.split("-")[0]] ?? "en");

    i18n.on("languageChanged", handleLanguageChanged);
    return () => {
      i18n.off("languageChanged", handleLanguageChanged);
    };
  }, [i18n]);

  const routedContent = (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route
          path="/login"
          element={
            <Suspense fallback={null}>
              <LoginPage />
            </Suspense>
          }
        />
        <Route
          path="/*"
          element={
            <AuthGuard authStatus={backendInfo.authStatus}>
              <MainLayout />
            </AuthGuard>
          }
        />
      </Routes>
    </BrowserRouter>
  );

  return (
    <>
      <GlobalStyle />
      <ConfigProvider
        {...selectedTheme}
        prefix="qwenpaw"
        prefixCls="qwenpaw"
        locale={antdLocale}
        theme={{
          ...(selectedTheme as { theme?: ThemeConfig }).theme,
          algorithm: isDark
            ? antdTheme.darkAlgorithm
            : antdTheme.defaultAlgorithm,
          token: {
            colorPrimary: "#FF7F16",
          },
        }}
      >
        <AntdApp>
          <ApprovalProvider>{routedContent}</ApprovalProvider>
        </AntdApp>
      </ConfigProvider>
    </>
  );
}

function App() {
  return (
    <ThemeProvider>
      <BackendModeRouter />
    </ThemeProvider>
  );
}

function BackendModeRouter() {
  const [backendInfo, setBackendInfo] = useState<
    "loading" | "error" | BackendInfo
  >("loading");
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    setBackendInfo("loading");
    setErrorMessage("");
    resolveBackendInfo()
      .then((nextInfo) => {
        if (!cancelled) setBackendInfo(nextInfo);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setErrorMessage(
          error instanceof Error ? error.message : "Backend detection failed",
        );
        setBackendInfo("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (backendInfo === "loading") {
    return null;
  }
  if (backendInfo === "error") {
    return <div>Backend error: {errorMessage}</div>;
  }
  return (
    <PluginProvider>
      <AppInner backendInfo={backendInfo} />
    </PluginProvider>
  );
}

export default App;
