import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ImageIcon,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { aprovarPrevia, reprovarPrevia, reprocessarPrevia, type RespostaPrevias } from "@/lib/api";

function Miniatura({ id, nome }: { id: string; nome: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [erro, setErro] = useState(false);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    if (!id) return;
    const controlador = new AbortController();
    setUrl(null);
    setErro(false);
    setCarregando(true);
    import("@/lib/api")
      .then(({ obterImagemPrevia }) => obterImagemPrevia(id, controlador.signal))
      .then((blob) => {
        setUrl(URL.createObjectURL(blob));
        setCarregando(false);
      })
      .catch((causa: unknown) => {
        if (causa instanceof DOMException && causa.name === "AbortError") return;
        setErro(true);
        setCarregando(false);
      });
    return () => {
      controlador.abort();
    };
  }, [id]);

  if (erro) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg bg-muted p-8 text-sm text-muted-foreground">
        <AlertCircle className="h-4 w-4" aria-hidden="true" />
        Não foi possível carregar a prévia.
      </div>
    );
  }
  if (carregando || !url) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-lg bg-muted p-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Carregando prévia...
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={`Prévia anonimizada de ${nome}`}
      className="max-h-96 w-full rounded-lg border border-border bg-white object-contain"
    />
  );
}

type AcaoItem = "aprovar" | "reprovar" | "reprocessar";

/**
 * Revisão humana da anonimização antes de qualquer chamada ao MCP.
 *
 * Somente as imagens explicitamente selecionadas seguem para o OCR externo.
 * Nenhuma imagem é pré-selecionada. Cada linha tem ações individuais de
 * aprovar, reprovar e reprocessar.
 */
export function RevisaoAnonimizacaoDialog({
  aberto,
  previa,
  enviando,
  onFechar,
  onAprovar,
}: {
  aberto: boolean;
  previa: RespostaPrevias | null;
  enviando: boolean;
  onFechar: () => void;
  onAprovar: (ids: string[]) => void;
}) {
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [processando, setProcessando] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!aberto || !previa) return;
    // Nenhuma pré-selecao: o usuario deve marcar explicitamente cada imagem.
    setSelecionados(new Set());
  }, [aberto, previa]);

  const aprovadas = previa?.itens.filter((item) => item.status === "aprovada") ?? [];
  const aguardandoTratamento = previa?.itens.filter(
    (item) => item.status === "aguardando_tratamento"
  ) ?? [];
  const bloqueadas = previa?.itens.filter(
    (item) => item.status !== "aprovada" && item.status !== "aguardando_tratamento"
  ) ?? [];

  const alternar = (id: string) => {
    setSelecionados((atual) => {
      const proximo = new Set(atual);
      if (proximo.has(id)) proximo.delete(id);
      else proximo.add(id);
      return proximo;
    });
  };

  const estaProcessando = (id: string) => processando.has(id);

  // "Selecionar todas" e uma acao explicita do usuario: so marca as imagens
  // aprovadas que nao estao em reprocessamento.
  const selecionaveis = aprovadas
    .filter((item) => item.id && !processando.has(item.id))
    .map((item) => item.id);
  const todasMarcadas =
    selecionaveis.length > 0 && selecionaveis.every((id) => selecionados.has(id));

  const alternarTodas = () => {
    setSelecionados(todasMarcadas ? new Set() : new Set(selecionaveis));
  };

  const executarAcao = useCallback(async (id: string, acao: AcaoItem) => {
    setProcessando((atual) => new Set([...atual, id]));
    try {
      if (acao === "aprovar") {
        await aprovarPrevia(id);
      } else if (acao === "reprovar") {
        await reprovarPrevia(id);
      } else if (acao === "reprocessar") {
        await reprocessarPrevia(id);
      }
    } catch {
      // Erro ja e exibido pelo hook (aoFalhar). Aqui apenas limpa o estado
      // de processamento para nao travar o botao.
    } finally {
      setProcessando((atual) => {
        const proximo = new Set(atual);
        proximo.delete(id);
        return proximo;
      });
    }
  }, []);

  const selecionadosArray = Array.from(selecionados);

  return (
    <Dialog
      open={aberto}
      onOpenChange={(valor) => {
        if (!valor && !enviando) onFechar();
      }}
    >
      <DialogContent className="flex max-h-[90vh] w-[calc(100vw-2rem)] max-w-3xl flex-col gap-4 overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 pr-8 text-left">
            <ShieldCheck className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
            Revisar anonimização antes do OCR
          </DialogTitle>
          <DialogDescription className="text-left">
            Confira se o cabeçalho pessoal foi removido da imagem. Selecione explicitamente cada
            imagem que deseja enviar para o serviço externo de OCR. Nenhuma imagem é
            pré-selecionada.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-start gap-2 rounded-lg bg-muted p-3 text-sm text-muted-foreground">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            O conteúdo dentro da tabela (horários, <strong>Falta</strong>, <strong>Férias</strong>,{" "}
            <strong>Justificado</strong> e a coluna <strong>Assinatura</strong>) é preservado. Abra
            cada prévia e confirme visualmente antes de continuar.
          </p>
        </div>

        {aprovadas.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">
              {selecionados.size} de {aprovadas.length} selecionada(s)
            </span>
            <button
              type="button"
              onClick={alternarTodas}
              disabled={enviando || selecionaveis.length === 0}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-primary px-3 py-1.5 text-sm font-medium text-primary transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              {todasMarcadas ? "Desmarcar todas" : "Selecionar todas"}
            </button>
          </div>
        )}

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          {
            aprovadas.length === 0 &&
            aguardandoTratamento.length === 0 &&
            bloqueadas.length === 0 && (
              <p className="rounded-lg bg-muted p-6 text-center text-sm text-muted-foreground">
                Nenhuma imagem foi processada.
              </p>
            )
          }

          {aprovadas.map((item) => {
            const marcado = selecionados.has(item.id);
            const estadoTexto = item.estado || item.status;
            const versaoTexto = item.versao ? ` v${item.versao}` : "";

            return (
              <div
                key={item.id}
                className={`rounded-xl border p-4 ${marcado ? "border-primary bg-accent/40" : "border-border"}`}
              >
                <label className="flex cursor-pointer items-center gap-3">
                  <input
                    type="checkbox"
                    checked={marcado}
                    onChange={() => alternar(item.id)}
                    disabled={estaProcessando(item.id)}
                    className="h-4 w-4 accent-[hsl(var(--primary))]"
                  />
                  <span className="flex items-center gap-2 font-medium">
                    <ImageIcon className="h-4 w-4 text-primary" aria-hidden="true" />
                    {item.nome}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {marcado ? "será enviada" : "não será enviada"}
                  </span>
                </label>
                <div className="mt-3">
                  <Miniatura id={item.id} nome={item.nome} />
                  <p className="mt-2 text-xs text-muted-foreground">
                    Estado: {estadoTexto}{versaoTexto}
                  </p>
                  <div className="mt-2 flex gap-1">
                    <button
                      type="button"
                      onClick={() => executarAcao(item.id, "aprovar")}
                      disabled={
                        enviando ||
                        estaProcessando(item.id) ||
                        item.status !== "aprovada"
                      }
                      className="inline-flex items-center justify-center rounded-lg border border-primary px-2 py-1 text-xs font-medium text-primary transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {estaProcessando(item.id) ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                      )}
                      Aprovar
                    </button>
                    <button
                      type="button"
                      onClick={() => executarAcao(item.id, "reprovar")}
                      disabled={enviando || estaProcessando(item.id)}
                      className="inline-flex items-center justify-center rounded-lg border border-destructive/40 px-2 py-1 text-xs font-medium text-destructive transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {estaProcessando(item.id) ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <XCircle className="h-3 w-3" aria-hidden="true" />
                      )}
                      Reprovar
                    </button>
                    <button
                      type="button"
                      onClick={() => executarAcao(item.id, "reprocessar")}
                      disabled={enviando || estaProcessando(item.id)}
                      className="inline-flex items-center justify-center rounded-lg border border-primary px-2 py-1 text-xs font-medium text-primary transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {estaProcessando(item.id) ? (
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <RefreshCw className="h-3 w-3" aria-hidden="true" />
                      )}
                      Reprocessar
                    </button>
                  </div>
                </div>
              </div>
            );
          })}

          {aguardandoTratamento.map((item) => (
            <div
              key={item.id}
              className="rounded-xl border border-warning/40 bg-warning/5 p-4"
            >
              <p className="flex items-center gap-2 font-medium text-warning-foreground">
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                {item.nome} — aguardando tratamento
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {item.codigo ? `Código: ${item.codigo}. ` : ""}
                {item.mensagem || "O tratamento automático não pôde ser executado."}
                {" "}
                Reinstale o PaddleOCR/PP-StructureV3 e use Reprocessar para gerar
                uma nova prévia protegida.
              </p>
              <div className="mt-2 flex gap-1">
                <button
                  type="button"
                  onClick={() => executarAcao(item.id, "reprocessar")}
                  disabled={enviando || estaProcessando(item.id)}
                  className="inline-flex items-center justify-center rounded-lg border border-warning px-2 py-1 text-xs font-medium text-warning-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {estaProcessando(item.id) ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                  ) : (
                    <RefreshCw className="h-3 w-3" aria-hidden="true" />
                  )}
                  Reprocessar
                </button>
                <button
                  type="button"
                  onClick={() => executarAcao(item.id, "reprovar")}
                  disabled={enviando || estaProcessando(item.id)}
                  className="inline-flex items-center justify-center rounded-lg border border-destructive/40 px-2 py-1 text-xs font-medium text-destructive transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {estaProcessando(item.id) ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                  ) : (
                    <XCircle className="h-3 w-3" aria-hidden="true" />
                  )}
                  Reprovar
                </button>
              </div>
            </div>
          ))}

          {bloqueadas.map((item, indice) => (
            <div
              key={`${item.nome}-${indice}`}
              className="rounded-xl border border-destructive/40 bg-destructive/5 p-4"
            >
              <p className="flex items-center gap-2 font-medium text-destructive">
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                {item.nome} — bloqueada
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {item.codigo ? `Código: ${item.codigo}. ` : ""}
                {item.mensagem || "Não foi possível anonimizar com segurança."}
                Esta imagem não será enviada ao OCR.
              </p>
            </div>
          ))}

          {previa && previa.avisos.length > 0 && (
            <div className="rounded-lg bg-warning p-3 text-sm text-warning-foreground">
              {previa.avisos.map((aviso, indice) => (
                <p key={indice}>{aviso}</p>
              ))}
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <button
            type="button"
            onClick={onFechar}
            disabled={enviando}
            className="inline-flex items-center justify-center rounded-lg border border-border px-4 py-2 text-sm font-medium transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => onAprovar(selecionadosArray)}
            disabled={enviando || selecionadosArray.length === 0}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {enviando ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            )}
            Enviar {selecionadosArray.length} aprovada(s) para OCR
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
