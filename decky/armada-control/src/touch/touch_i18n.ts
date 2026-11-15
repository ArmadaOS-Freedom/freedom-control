// The touch page was written for en/zh only, so every other Controller locale
// reads it in English, exactly like the TF page.

import { translate } from "./i18n.ts";
import type { Language, TranslationKey } from "./i18n";
import type { Locale } from "../i18n";

export function touchLanguage(locale: Locale): Language {
  return locale === "zh-CN" ? "zh" : "en";
}

export function touchText(
  language: Language,
  key: TranslationKey,
  variables?: Record<string, string | number>,
): string {
  return translate(language, key, variables);
}
