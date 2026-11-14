/**
 * What the Sao Khuê About card says. Vietnamese for every interface language: the
 * edition is VPBank's own, and these lines describe the product and its legal
 * origin rather than a control to translate.
 */

export const PRODUCT = 'Sao Khuê'
export const PRODUCT_TAGLINE = 'Nền tảng quản trị AI Agent tự hành của VPBank'

/** The open-source project this edition is developed from. */
export const UPSTREAM_NAME = 'KiroCrew'
/**
 * The upstream release the last merge brought in (its `__version__`). Update it
 * with each `git merge upstream/main`, in the same commit.
 */
export const UPSTREAM_BASE_VERSION = '0.13.0'

export const LICENSE_NAME = 'Apache License 2.0'
/** Required by the licence: the upstream notice stays with every copy. */
export const UPSTREAM_NOTICE = 'Copyright Amazon.com, Inc. or its affiliates'

/** The default data home; `SAOKHUE_HOME` (or `KIROCREW_HOME`) overrides it. */
export const DATA_HOME = '~/.vpbank/saokhue'

export const LABELS = {
  product: 'Sản phẩm',
  appVersion: 'Phiên bản ứng dụng',
  gatewayVersion: 'Phiên bản gateway',
  platform: 'Hệ điều hành',
  base: 'Phát triển từ',
  license: 'Giấy phép',
  notice: 'Ghi nhận bản quyền',
  dataHome: 'Thư mục dữ liệu',
  updates: 'Cập nhật',
} as const

export const ATTRIBUTION =
  `${PRODUCT} là bản phát triển kế thừa (fork) từ dự án mã nguồn mở ${UPSTREAM_NAME}, ` +
  `được phân phối và cấp phép theo ${LICENSE_NAME}.`

export const DATA_HOME_NOTE = 'đổi bằng biến môi trường SAOKHUE_HOME'
export const NOTICE_NOTE = 'tệp LICENSE và NOTICE đi kèm bản cài đặt'
export const UPDATES_OFF = 'Tắt kiểm tra cập nhật tự động'
export const UPDATES_MANAGED_BY = (owner: string): string => `Phân phối bởi ${owner}`

const PLATFORM_NAMES: Record<string, string> = {
  win32: 'Windows',
  darwin: 'macOS',
  linux: 'Linux',
}

/** A friendly platform name, or the raw value for one this table does not know. */
export function platformName(platform: string): string {
  return PLATFORM_NAMES[platform] ?? platform
}
