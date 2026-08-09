import type { ReactNode } from "react";

type ButtonProps = {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
};

export function Button({
  children,
  className = "secondary",
  onClick,
  disabled = false,
  title
}: ButtonProps) {
  return (
    <button
      className={`btn ${className}`}
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}
