// 统一细线条图标库（Linear 风格：24 viewBox / stroke 1.7 / round join）
import type { ReactNode } from "react";

export type IconProps = { size?: number; className?: string };

function S({ size = 16, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const IconSearch = (p: IconProps) => (
  <S {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></S>
);

export const IconImage = (p: IconProps) => (
  <S {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2.5" />
    <circle cx="8.5" cy="9.5" r="1.6" />
    <path d="m3.5 17 4.8-4.8a1.4 1.4 0 0 1 2 0l3.2 3.2 2-2a1.4 1.4 0 0 1 2 0l3.5 3.5" />
  </S>
);

export const IconClose = (p: IconProps) => (
  <S {...p}><path d="M6 6l12 12M18 6L6 18" /></S>
);

export const IconSettings = (p: IconProps) => (
  <S {...p}>
    <path d="M3 5h7M16 5h5M3 12h3M12 12h9M3 19h11M20 19h1" />
    <circle cx="13" cy="5" r="2.2" />
    <circle cx="9" cy="12" r="2.2" />
    <circle cx="17" cy="19" r="2.2" />
  </S>
);

export const IconKeyboard = (p: IconProps) => (
  <S {...p}>
    <rect x="2" y="6" width="20" height="12" rx="2.5" />
    <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h.01M18 14h.01M9 14h6" />
  </S>
);

export const IconFolder = (p: IconProps) => (
  <S {...p}>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2.2 2.5h8.8A1.5 1.5 0 0 1 21 10v7.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z" />
  </S>
);

export const IconFolderOpen = (p: IconProps) => (
  <S {...p}>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2.2 2.5h8.8A1.5 1.5 0 0 1 21 10v1H6.5a2 2 0 0 0-1.9 1.4L3 18Z" />
    <path d="m6.5 11.5h14.3l-2 7a1.5 1.5 0 0 1-1.4 1H5.6a1.2 1.2 0 0 1-1.1-1.6l2-5.4a2 2 0 0 1 0-1Z" />
  </S>
);

export const IconList = (p: IconProps) => (
  <S {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </S>
);

export const IconGrid = (p: IconProps) => (
  <S {...p}>
    <rect x="4" y="4" width="7" height="7" rx="1.5" />
    <rect x="13" y="4" width="7" height="7" rx="1.5" />
    <rect x="4" y="13" width="7" height="7" rx="1.5" />
    <rect x="13" y="13" width="7" height="7" rx="1.5" />
  </S>
);

export const IconSimilar = (p: IconProps) => (
  <S {...p}>
    <path d="m17 2 4 4-4 4" />
    <path d="M3 11v-1a4 4 0 0 1 4-4h14" />
    <path d="m7 22-4-4 4-4" />
    <path d="M21 13v1a4 4 0 0 1-4 4H3" />
  </S>
);

export const IconClock = (p: IconProps) => (
  <S {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></S>
);

export const IconCopy = (p: IconProps) => (
  <S {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5" />
  </S>
);

export const IconDownload = (p: IconProps) => (
  <S {...p}><path d="M12 3v12m0 0 4-4m-4 4-4-4" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></S>
);

export const IconFile = (p: IconProps) => (
  <S {...p}>
    <path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5Z" />
    <path d="M13.5 3V8.5H19" />
  </S>
);

export const IconLayers = (p: IconProps) => (
  <S {...p}>
    <path d="m12 3 8.5 4.5L12 12 3.5 7.5Z" />
    <path d="m3.5 12.5 8.5 4.5 8.5-4.5" />
    <path d="m3.5 17 8.5 4.5 8.5-4.5" />
  </S>
);

export const IconDatabase = (p: IconProps) => (
  <S {...p}>
    <ellipse cx="12" cy="5.5" rx="8" ry="2.8" />
    <path d="M4 5.5v13c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8v-13" />
    <path d="M4 12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8" />
  </S>
);

export const IconEye = (p: IconProps) => (
  <S {...p}>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
    <circle cx="12" cy="12" r="3" />
  </S>
);

export const IconActivity = (p: IconProps) => (
  <S {...p}><path d="M3 12h4l2.5-7 5 14L17 12h4" /></S>
);

export const IconPlus = (p: IconProps) => (
  <S {...p}><path d="M12 5v14M5 12h14" /></S>
);

export const IconRefresh = (p: IconProps) => (
  <S {...p}>
    <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6l2.5 2.4" />
    <path d="M20.5 3v5.5H15" />
  </S>
);

export const IconTrash = (p: IconProps) => (
  <S {...p}>
    <path d="M4 7h16M10 4h4M6 7l1 13a1.5 1.5 0 0 0 1.5 1.4h7A1.5 1.5 0 0 0 17 20l1-13" />
    <path d="M10 11v6M14 11v6" />
  </S>
);

export const IconStop = (p: IconProps) => (
  <S {...p}><rect x="6.5" y="6.5" width="11" height="11" rx="1.5" /></S>
);

export const IconChevronLeft = (p: IconProps) => (
  <S {...p}><path d="m14.5 5.5-6.5 6.5 6.5 6.5" /></S>
);

export const IconChevronRight = (p: IconProps) => (
  <S {...p}><path d="m9.5 5.5 6.5 6.5-6.5 6.5" /></S>
);

export const IconZoomIn = (p: IconProps) => (
  <S {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5M8.5 11h5M11 8.5v5" /></S>
);

export const IconZoomOut = (p: IconProps) => (
  <S {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5M8.5 11h5" /></S>
);

export const IconMaximize = (p: IconProps) => (
  <S {...p}>
    <path d="M8 3.5H5A1.5 1.5 0 0 0 3.5 5v3M16 3.5h3A1.5 1.5 0 0 1 20.5 5v3M8 20.5H5A1.5 1.5 0 0 1 3.5 19v-3M16 20.5h3a1.5 1.5 0 0 0 1.5-1.5v-3" />
  </S>
);

export const IconSparkle = (p: IconProps) => (
  <S {...p}>
    <path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.2l-1.8-5.6L4.5 10.8 10.2 9Z" />
    <path d="M19 3.5v3M17.5 5h3" />
  </S>
);

export const IconUpload = (p: IconProps) => (
  <S {...p}><path d="M12 15V3m0 0 4 4m-4-4L8 7" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></S>
);

export const IconPlay = (p: IconProps) => (
  <S {...p}><path d="M7.5 5.5v13l11-6.5Z" /></S>
);
export const IconCheck = (p: IconProps) => (
  <S {...p}><path d="m4.5 12.5 5 5 10-11" /></S>
);