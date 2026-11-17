#!/bin/bash
# CEK SEMUA TABEL - produksi vs SEMUA node cluster: JUMLAH baris + ISI setiap baris, SELURUH tabel.
#
#   sudo bash /tmp/cek-semua-tabel.sh                 di db-node1 atau db-node2 (ditunggu di layar)
#   sudo LATAR=1 bash /tmp/cek-semua-tabel.sh         jalan di latar; SSH boleh ditutup  (DISARANKAN)
#   sudo MULAI=01:00 LATAR=1 bash ...                 sandi diisi sekarang, cek baru mulai pukul 01:00
#   (pengaturan ditulis SESUDAH kata sudo; bila sudah root, sudo boleh dihilangkan)
#   Terjadwal (cron, tanpa ditunggu): sandi di /root/.pgpass, lalu  /etc/cron.d/cek-semua-tabel
#   sudo MAKS_GB=5 bash ...                                   lewati tabel > 5 GB (untuk percobaan pertama)
#   sudo TABEL_FILE=/var/log/cek-semua/<waktu>/ulang.txt bash ...   cek ulang hanya tabel yang belum COCOK
#
# Bila pelapor HA Helper terpasang di node ini (/etc/hahelper-lapor.conf), kemajuan & hasil
# otomatis tampil di dashboard, tab "Cek Semua Tabel". Tanpa pelapor, hasil hanya di berkas.
#
# HANYA MEMBACA. Tidak ada yang diubah atau dijeda, di produksi maupun di cluster.
# Membaca SELURUH isi tiap tabel (di produksi dan tiap node) -> JALANKAN SAAT SEPI (malam).
# Satu tabel dibaca bersamaan di produksi & node, satu tabel demi satu tabel, tanpa query paralel.
#
# Hasil per tabel:
#   COCOK    jumlah dan isi sama persis
#   BERUBAH  sempat beda karena data sedang diubah aplikasi, saat dicek ulang sudah sama  -> OK
#   AKTIF    tetap beda, tapi tabel memang terus diubah selama dicek -> ulangi saat sepi / Hari-H
#   BEDA     tetap beda padahal tabel TIDAK sedang diubah  -> MASALAH, kirim hasilnya
#   UNLOGGED tabel UNLOGGED: TIDAK ikut direplikasi, isinya HILANG setelah pindah ke cluster
#   GAGAL    tidak bisa dibaca (lihat galat.txt)
#
# Selain tabel juga dicek: posisi replikasi tiap node, large object (bila dipakai), dan
# SEQUENCE (nilai di node tidak boleh di bawah produksi; boleh di atas, itu sifat replikasi).
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DB=${DB:-appdb}; USR=${USR:-postgres}
PROD_HOST=${PROD_HOST:-10.20.0.5}; PROD_PORT=${PROD_PORT:-5432}
CADANGAN="db-node1:10.10.0.11:5433 db-node2:10.10.0.12:5433"
ULANG=${ULANG:-2}; TUNGGU=${TUNGGU:-120}; MAKS_GB=${MAKS_GB:-0}; LATAR=${LATAR:-0}
HASIL=${HASIL:-/var/log/cek-semua/$(date +%Y%m%d-%H%M)}
mkdir -p "$HASIL" || exit 1
cd /tmp
HURL=${HELPER_URL:-$(sed -n 's/^URL=//p' /etc/hahelper-lapor.conf 2>/dev/null)}
HTOKEN=${HELPER_TOKEN:-$(sed -n 's/^TOKEN=//p' /etc/hahelper-lapor.conf 2>/dev/null)}
RUN="$(hostname -s)-$(basename "$HASIL")"
# kirim JENIS ISI : kabar ke dashboard HA Helper (bila ada); gagal kirim tidak menghentikan cek
kirim() { [ -n "$HURL" ] && [ -n "$HTOKEN" ] || return 0
  printf '%s\n' "$2" | curl -s -m 5 $HELPER_CURL_OPTS -o /dev/null -X POST -H "X-Token: $HTOKEN" -H "X-Run: $RUN" -H "X-Jenis: $1" \
    -H "Content-Type: text/plain" --data-binary @- "${HURL%/}/api/cekpenuh/lapor" 2>/dev/null || true; }

SESI="SET TimeZone='Asia/Jakarta'; SET DateStyle='ISO, YMD'; SET IntervalStyle='postgres'; SET extra_float_digits=1;
SET bytea_output='hex'; SET statement_timeout=0; SET max_parallel_workers_per_gather=0; SET application_name='cek-semua-tabel';"
SIDIK="coalesce(sum(('x'||substr(md5(t::text),1,16))::bit(64)::bigint),0)"

# ---------------------------------------------------------------- node cluster
if [ -z "$NODES" ]; then
  NODES=$(patronictl -c /etc/patroni/config.yml list -f json 2>/dev/null | python3 -c '
import json, sys
try:
    for x in json.load(sys.stdin):
        h = x.get("Host", ""); hp = h.split(":")
        print("%s:%s:%s" % (x["Member"], hp[0], hp[1] if len(hp) > 1 else "5433"))
except Exception: pass')
  [ -n "$NODES" ] || NODES=$CADANGAN
fi

# Sandi: ditanya di layar, ATAU (untuk cron) diambil psql dari /root/.pgpass
ACUAN=Produksi
if [ "$PROD_HOST" = pemimpin ]; then   # setelah Hari-H: acuan = pemimpin cluster saat ini
  L=$(patronictl -c /etc/patroni/config.yml list -f json 2>/dev/null | python3 -c '
import json, sys
try:
    for x in json.load(sys.stdin):
        if x.get("Role") in ("Leader", "Standby Leader"):
            h = x.get("Host", ""); hp = h.split(":")
            print("%s:%s" % (hp[0], hp[1] if len(hp) > 1 else "5433")); break
except Exception: pass')
  [ -n "$L" ] || { echo "Pemimpin cluster tidak ditemukan (patronictl)."; exit 1; }
  PROD_HOST=${L%:*}; PROD_PORT=${L#*:}; ACUAN=Pemimpin
fi
if [ -z "$PW" ]; then
  if [ -t 0 ] && [ "${PAKAI_PGPASS:-0}" != 1 ]; then read -s -p "Sandi $USR (produksi & cluster): " PW; echo
  elif [ ! -s "${PGPASSFILE:-$HOME/.pgpass}" ]; then
    echo "Tidak ada layar untuk menanyakan sandi dan ${PGPASSFILE:-$HOME/.pgpass} tidak ada. Lihat cara pasang jadwal."; exit 1
  fi
fi
[ -n "$PW" ] && export PW
q() { env ${PW:+PGPASSWORD="$PW"} psql -w -h "$1" -p "$2" -U $USR -d $DB -Atq -F'|' -c "$3" 2>>"$HASIL/galat.txt"; }

q $PROD_HOST $PROD_PORT "SELECT 1" >/dev/null || { echo "GAGAL tersambung ke $ACUAN $PROD_HOST:$PROD_PORT"; tail -3 "$HASIL/galat.txt"; exit 1; }
HIDUP=()
for n in $NODES; do
  IFS=: read nama host port <<< "$n"
  [ "$host:$port" = "$PROD_HOST:$PROD_PORT" ] && continue   # node acuan sendiri (pemimpin) tidak dibandingkan
  if [ "$(q $host $port "SELECT pg_is_in_recovery()")" = "t" ]; then HIDUP+=("$nama:$host:$port")
  else echo "  $nama ($host:$port) tidak terjangkau / bukan standby - DILEWATI"; fi
done
[ ${#HIDUP[@]} -gt 0 ] || { echo "Tidak ada node cluster yang bisa dibaca."; exit 1; }

# ---------------------------------------------------------------- fungsi
# hitung HOST PORT TABEL JENIS -> "jumlah|sidik" atau "GAGAL"; dicoba 3x (query di replica bisa dibatalkan replikasi)
hitung() { local from="ONLY $3" out k
  [ "$4" = m ] && from="$3"
  for k in 1 2 3; do
    out=$(env ${PW:+PGPASSWORD="$PW"} psql -w -h "$1" -p "$2" -U $USR -d $DB -Atq -F'|' -v ON_ERROR_STOP=1 2>>"$HASIL/galat.txt" <<SQL
$SESI
SELECT count(*)||'|'||$SIDIK FROM $from t;
SQL
)
    [ -n "$out" ] && { echo "$out"; return; }
    echo "  ($(date +%T) $3 di $1 dicoba lagi)" >> "$HASIL/galat.txt"; sleep 3
  done
  echo GAGAL
}
# jumlah perubahan (insert+update+delete) tabel di produksi sejak statistik direset
ubah() { local r=${1//\'/\'\'}
  q $PROD_HOST $PROD_PORT "SELECT coalesce((SELECT n_tup_ins+n_tup_upd+n_tup_del FROM pg_stat_user_tables WHERE relid='$r'::regclass),0)"; }
# tunggu semua node menerapkan data sampai posisi produksi saat ini
susul() { local lsn i n nama host port
  lsn=$(q $PROD_HOST $PROD_PORT "SELECT pg_current_wal_lsn()")
  for n in "${HIDUP[@]}"; do IFS=: read nama host port <<< "$n"; i=0
    while [ "$(q $host $port "SELECT pg_last_wal_replay_lsn() >= '$lsn'::pg_lsn")" != "t" ]; do
      i=$((i+1)); [ $i -gt $TUNGGU ] && { echo "$nama"; return; }; sleep 1; done
  done; }
# satu putaran: produksi & semua node dibaca BERSAMAAN. Hasil: baris "label|jumlah|sidik" di $D/putaran
putaran() { local t=$1 j=$2 n nama host port
  rm -f "$D"/p.*
  ( hitung $PROD_HOST $PROD_PORT "$t" "$j" > "$D/p.0-produksi" ) &
  for n in "${HIDUP[@]}"; do IFS=: read nama host port <<< "$n"
    ( hitung $host $port "$t" "$j" > "$D/p.1-$nama" ) &
  done; wait
  for f in "$D"/p.*; do echo "$(basename "$f" | cut -d- -f2-)|$(cat "$f")"; done > "$D/putaran"; }
sama() { [ "$(cut -d'|' -f2- "$D/putaran" | sort -u | wc -l)" = 1 ] && ! grep -q GAGAL "$D/putaran"; }

utama() {
  if [ -n "$MULAI" ]; then
    tunda=$(( $(date -d "$MULAI" +%s) - $(date +%s) )); [ $tunda -lt 0 ] && tunda=$((tunda + 86400))
    echo "Menunggu sampai $MULAI ($((tunda / 60)) menit lagi)..."; sleep $tunda
  fi
  D=$(mktemp -d); trap 'rm -rf "$D"' EXIT
  echo "== CEK SEMUA TABEL  $(date '+%F %T') =="
  printf "   %-9s: %s   database %s\n" "$ACUAN" "$PROD_HOST:$PROD_PORT" "$DB"
  echo "   Node     : ${HIDUP[*]}"
  echo "   Hasil    : $HASIL"
  [ -n "$HURL" ] && echo "   Dashboard: $HURL (tab Cek Semua Tabel)"
  # ------------------------------------------------ posisi replikasi
  echo; echo "== 0. POSISI REPLIKASI ============================================="
  plsn=$(q $PROD_HOST $PROD_PORT "SELECT pg_current_wal_lsn()")
  for n in "${HIDUP[@]}"; do IFS=: read nama host port <<< "$n"
    printf "   %-13s: %s\n" "$nama" "$(q $host $port "SELECT 'tertinggal '||pg_size_pretty(greatest(pg_wal_lsn_diff('$plsn', pg_last_wal_replay_lsn()),0))||', transaksi terakhir diterapkan '||coalesce(to_char(pg_last_xact_replay_timestamp(),'YYYY-MM-DD HH24:MI:SS'),'-')")"
  done
  SELISIH_NOL=1; tl=$(susul)
  if [ -n "$tl" ]; then SELISIH_NOL=0; echo "   >> $tl BELUM menyusul posisi ${ACUAN,,} setelah ${TUNGGU} detik"
  else echo "   >> semua node sudah sampai posisi ${ACUAN,,} saat ini (SELISIH NOL)"; fi
  # ------------------------------------------------ daftar tabel
  INV="SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname), c.relkind, c.relpersistence,
         pg_total_relation_size(c.oid), pg_size_pretty(pg_total_relation_size(c.oid))
       FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE c.relkind IN ('r','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
         AND n.nspname !~ '^pg_(toast|temp)' ORDER BY 4, 1"
  q $PROD_HOST $PROD_PORT "$INV" > "$HASIL/daftar-produksi.txt"
  echo; echo "== 1. KELENGKAPAN TABEL ============================================"
  echo "   $ACUAN : $(wc -l < "$HASIL/daftar-produksi.txt") tabel, total $(q $PROD_HOST $PROD_PORT "SELECT pg_size_pretty(pg_database_size('$DB'))")"
  for n in "${HIDUP[@]}"; do IFS=: read nama host port <<< "$n"
    q $host $port "$INV" | cut -d'|' -f1 | sort > "$D/inv-$nama"
    kurang=$(cut -d'|' -f1 "$HASIL/daftar-produksi.txt" | sort | comm -23 - "$D/inv-$nama")
    lebih=$(cut -d'|' -f1 "$HASIL/daftar-produksi.txt" | sort | comm -13 - "$D/inv-$nama")
    printf "   %-13s: %s tabel" "$nama" "$(wc -l < "$D/inv-$nama")"
    KLG+="$nama $(wc -l < "$D/inv-$nama") tabel$([ -z "$kurang$lebih" ] && echo ' sama' || echo " BEDA (tidak ada di node: $(echo $kurang | head -c 300); hanya di node: $(echo $lebih | head -c 300))"); "
    if [ -z "$kurang$lebih" ]; then echo "  - daftar SAMA dengan ${ACUAN,,}"
    else KL_BEDA=1; echo "  - BEDA!"; [ -n "$kurang" ] && echo "      tidak ada di node: $(echo $kurang)"; [ -n "$lebih" ] && echo "      hanya ada di node: $(echo $lebih)"; fi
  done
  # ------------------------------------------------ pilih tabel
  cp "$HASIL/daftar-produksi.txt" "$D/cek"
  if [ -n "$TABEL_FILE" ]; then
    awk -F'|' 'NR==FNR{w[$1]=1;next} w[$1]' "$TABEL_FILE" "$HASIL/daftar-produksi.txt" > "$D/cek"
    echo "   Hanya tabel dari $TABEL_FILE: $(wc -l < "$D/cek") tabel"
  fi
  if [ "$MAKS_GB" != 0 ]; then
    awk -F'|' -v m="$MAKS_GB" '$4 > m*1073741824 {print "      dilewati (>" m " GB): " $1 " " $5}' "$D/cek" | tee -a "$HASIL/dilewati.txt"
    awk -F'|' -v m="$MAKS_GB" '$4 <= m*1073741824' "$D/cek" > "$D/cek2"; mv "$D/cek2" "$D/cek"
  fi
  if [ -z "$TABEL_FILE" ] && [ "$(q $PROD_HOST $PROD_PORT "SELECT count(*) > 0 FROM pg_largeobject_metadata")" = t ]; then
    q $PROD_HOST $PROD_PORT "SELECT 'pg_catalog.pg_largeobject|r|p|'||pg_total_relation_size('pg_largeobject')||'|'||pg_size_pretty(pg_total_relation_size('pg_largeobject'))" >> "$D/cek"
    echo "   + large object (pg_largeobject) ikut dicek"
  fi
  if [ "${CEPAT:-0}" = 1 ]; then : > "$D/cek"; echo "   CEK CEPAT: isi tabel tidak dibaca satu per satu (lihat cek penuh H-1)"; fi
  TOTAL=$(wc -l < "$D/cek"); BYTES=$(awk -F'|' '{s+=$4} END{printf "%.0f", s}' "$D/cek")
  kirim mulai "host=$(hostname -s)
produksi=$PROD_HOST:$PROD_PORT ($ACUAN)
database=$DB
node=$(for n in "${HIDUP[@]}"; do printf "%s," "${n%%:*}"; done | sed 's/,$//')
total=$TOTAL
bytes=$BYTES
filter=${TABEL_FILE:+hanya tabel dari $(basename "$TABEL_FILE")}${MAKS_GB:+$([ "$MAKS_GB" != 0 ] && echo " maks $MAKS_GB GB")}
kelengkapan=$KLG
hasil=$HASIL"
  echo; echo "== 2. ISI TIAP TABEL ($TOTAL tabel, dari yang terkecil) ==============="
  printf "   %-9s %-45s %10s %14s  %s\n" "No" "Tabel" "Ukuran" "Baris ${ACUAN,,}" "Hasil"
  echo "tabel|ukuran|hasil|baris_produksi|baris_node ($(for n in "${HIDUP[@]}"; do printf "%s " "${n%%:*}"; done | sed 's/ $//; s/ /,/g'))|catatan" > "$HASIL/hasil.csv"
  : > "$HASIL/ulang.txt"
  declare -A JML=(); i=0; SELESAI_B=0; T0=$(date +%s)
  while IFS='|' read -r t j p b ukuran; do
    i=$((i+1)); t1=$(date +%s); cat=""
    if [ "$p" = u ]; then
      st=UNLOGGED; cat="tabel UNLOGGED tidak direplikasi; isinya hilang setelah pindah"
      : > "$D/putaran"; jp="-"
    else
      a0=$(ubah "$t")
      tl=$(susul); [ -n "$tl" ] && cat="node $tl tertinggal > ${TUNGGU} dtk"
      putaran "$t" "$j"
      if sama; then st=COCOK
      else
        st=""
        for u in $(seq 1 $ULANG); do sleep 5; susul >/dev/null; putaran "$t" "$j"; sama && { st=BERUBAH; break; }; done
        if [ -z "$st" ]; then
          if grep -q GAGAL "$D/putaran"; then st=GAGAL
          elif [ "$(ubah "$t")" != "$a0" ]; then st=AKTIF; cat+="${cat:+; }tabel terus diubah selama dicek"
          else st=BEDA; fi
          [ "$st" != GAGAL ] && {
            if cut -d'|' -f2 "$D/putaran" | sort -u | wc -l | grep -qx 1; then cat+="${cat:+; }jumlah sama, ISI beda"
            else cat+="${cat:+; }JUMLAH beda"; fi; }
        fi
      fi
      jp=$(grep '^produksi|' "$D/putaran" | cut -d'|' -f2)
    fi
    JML[$st]=$(( ${JML[$st]:-0} + 1 ))
    nodes=$(for n in "${HIDUP[@]}"; do printf "%s," "$(grep "^${n%%:*}|" "$D/putaran" | cut -d'|' -f2)"; done)
    echo "$t|$ukuran|$st|$jp|${nodes%,}|$cat" >> "$HASIL/hasil.csv"
    kirim tabel "$t|$ukuran|$st|$jp|${nodes%,}|${cat//|/ }|$(( $(date +%s) - t1 ))|$b"
    case $st in COCOK|BERUBAH) ;; *) echo "$t" >> "$HASIL/ulang.txt";; esac
    SELESAI_B=$((SELESAI_B + b)); dt=$(( $(date +%s) - T0 )); sisa=""
    [ $SELESAI_B -gt 0 ] && [ $dt -gt 30 ] && sisa=" ~sisa $(( (BYTES - SELESAI_B) * dt / SELESAI_B / 60 )) mnt"
    tanda=""; case $st in BEDA|GAGAL|UNLOGGED) tanda="  <-- PERIKSA";; AKTIF) tanda="  (ulangi saat sepi)";; esac
    beda_node=""; [ "$st" != COCOK ] && [ "$st" != UNLOGGED ] && [ "$st" != BERUBAH ] && \
      beda_node=" [$(grep -v '^produksi|' "$D/putaran" | cut -d'|' -f1,2 | tr '\n' ' ' | sed 's/ $//')]"
    printf "   %-9s %-45s %10s %14s  %s%s%s  (%ss%s)\n" "$i/$TOTAL" "$t" "$ukuran" "${jp:--}" "$st" "$beda_node" "$tanda" \
      "$(( $(date +%s) - t1 ))" "$sisa"
  done < "$D/cek"

  # ------------------------------------------------ sequence
  if [ -z "$TABEL_FILE" ]; then
    echo; echo "== 2b. SEQUENCE (nilai di node harus >= ${ACUAN,,}) ===================="
    SQ="SELECT quote_ident(schemaname)||'.'||quote_ident(sequencename)||'|'||coalesce(last_value::text,'-') FROM pg_sequences ORDER BY 1"
    susul >/dev/null
    q $PROD_HOST $PROD_PORT "$SQ" > "$D/seq-produksi"
    sq_beda=0
    for n in "${HIDUP[@]}"; do IFS=: read nama host port <<< "$n"
      q $host $port "$SQ" > "$D/seq-$nama"
      hasil_sq=$(awk -F'|' 'NR==FNR{p[$1]=$2;next} {n[$1]=$2}
        END{for(k in p){ if(!(k in n)){print "tidak ada di node: " k; continue}
                         if(p[k]!="-" && (n[k]=="-" || n[k]+0 < p[k]+0)) print "TERTINGGAL " k " produksi=" p[k] " node=" n[k]}}' "$D/seq-produksi" "$D/seq-$nama")
      if [ -z "$hasil_sq" ]; then printf "   %-13s: %s sequence, semua sudah sama/di atas ${ACUAN,,}\n" "$nama" "$(wc -l < "$D/seq-$nama")"
      else
        # dicek ulang sekali: sequence bisa naik di produksi saat dibaca
        sleep 5; susul >/dev/null; q $PROD_HOST $PROD_PORT "$SQ" > "$D/seq-produksi"; q $host $port "$SQ" > "$D/seq-$nama"
        hasil_sq=$(awk -F'|' 'NR==FNR{p[$1]=$2;next} {n[$1]=$2}
          END{for(k in p){ if(!(k in n)){print "tidak ada di node: " k; continue}
                           if(p[k]!="-" && (n[k]=="-" || n[k]+0 < p[k]+0)) print "TERTINGGAL " k " produksi=" p[k] " node=" n[k]}}' "$D/seq-produksi" "$D/seq-$nama")
        if [ -z "$hasil_sq" ]; then printf "   %-13s: %s sequence, sama/di atas ${ACUAN,,} (setelah dicek ulang)\n" "$nama" "$(wc -l < "$D/seq-$nama")"
        else sq_beda=$((sq_beda + $(echo "$hasil_sq" | wc -l))); printf "   %-13s: BEDA\n" "$nama"; echo "$hasil_sq" | head -20 | sed 's/^/      /'
          echo "$hasil_sq" | sed "s/^/$nama: /" >> "$HASIL/sequence-beda.txt"; fi
      fi
    done
  fi
  kirim selesai "lama=$(( $(date +%s) - T0 ))"
  echo; echo "== 3. KESIMPULAN ($(date '+%F %T'), lama $(( ($(date +%s) - T0) / 60 )) menit) ======"
  for s in COCOK BERUBAH AKTIF BEDA UNLOGGED GAGAL; do [ -n "${JML[$s]}" ] && printf "   %-9s %s tabel\n" "$s" "${JML[$s]}"; done
  if [ "${CEPAT:-0}" = 1 ]; then
    printf "   %-22s %s\n" "Selisih ke ${ACUAN,,}:" "$([ "$SELISIH_NOL" = 1 ] && echo "NOL (OK)" || echo "BELUM NOL - JANGAN LANJUT")"
    printf "   %-22s %s\n" "Daftar tabel:" "$([ -z "$KL_BEDA" ] && echo "SAMA (OK)" || echo "BEDA - PERIKSA")"
    printf "   %-22s %s\n" "Sequence:" "$([ "${sq_beda:-0}" = 0 ] && echo "tidak tertinggal (OK)" || echo "$sq_beda tertinggal - PERIKSA")"
    [ "$SELISIH_NOL" = 1 ] && [ -z "$KL_BEDA" ] && [ "${sq_beda:-0}" = 0 ] && echo "   KESIMPULAN CEPAT: cluster sudah memuat seluruh data ${ACUAN,,} -> boleh lanjut."
  fi
  [ "${sq_beda:-0}" -gt 0 ] && printf "   %-9s %s sequence tertinggal (lihat sequence-beda.txt)\n" "SEQUENCE" "$sq_beda"
  if [ "${CEPAT:-0}" = 1 ]; then :
  elif [ -z "${JML[BEDA]}${JML[GAGAL]}${JML[UNLOGGED]}${JML[AKTIF]}" ] && [ "${sq_beda:-0}" = 0 ]; then
    echo "   SEMUA tabel yang dicek SAMA (jumlah dan isi) antara ${ACUAN,,} dan $([ "$ACUAN" = Pemimpin ] && echo replica || echo cluster)$([ -z "$TABEL_FILE" ] && echo ", sequence tidak tertinggal")."
  else
    [ -n "${JML[AKTIF]}" ] && echo "   AKTIF  : ulangi saat sepi / Hari-H setelah aplikasi berhenti:  TABEL_FILE=$HASIL/ulang.txt"
    [ -n "${JML[BEDA]}" ] && echo "   BEDA   : MASALAH - kirim $HASIL/hasil.csv"
    [ -n "${JML[UNLOGGED]}" ] && echo "   UNLOGGED: isinya TIDAK pindah ke cluster - putuskan dengan tim aplikasi"
    [ -n "${JML[GAGAL]}" ] && echo "   GAGAL  : lihat $HASIL/galat.txt"
  fi
  [ -s "$HASIL/dilewati.txt" ] && echo "   Dilewati karena ukuran: $(wc -l < "$HASIL/dilewati.txt") tabel (lihat dilewati.txt)"
  echo "   Rincian: $HASIL/hasil.csv"
  find "$(dirname "$HASIL")" -mindepth 1 -maxdepth 1 -type d -mtime +${SIMPAN_HARI:-60} -exec rm -rf {} + 2>/dev/null
}

if [ "$LATAR" = 1 ]; then
  ( trap '' HUP; utama ) > "$HASIL/log.txt" 2>&1 < /dev/null &
  disown
  echo "Berjalan di latar (PID $!)${MULAI:+, mulai pukul $MULAI}. SSH boleh ditutup."
  echo "  Pantau : tail -f $HASIL/log.txt"
  echo "  Hentikan: kill $!"
else
  utama 2>&1 | tee "$HASIL/log.txt"
fi
