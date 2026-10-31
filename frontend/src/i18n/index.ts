import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zhCN from "./zh-CN.json";
import enUS from "./en-US.json";

const stored = localStorage.getItem("bitssh.language");
const fallback = navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";

i18n.use(initReactI18next).init({
  resources: {
    "zh-CN": { translation: zhCN },
    "en-US": { translation: enUS },
  },
  lng: stored || fallback,
  fallbackLng: "en-US",
  interpolation: { escapeValue: false },
});

export function setLanguage(lang: string) {
  localStorage.setItem("bitssh.language", lang);
  void i18n.changeLanguage(lang);
}

export default i18n;
