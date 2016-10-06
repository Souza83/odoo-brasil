# -*- coding: utf-8 -*-
# © 2016 Danimar Ribeiro <danimaribeiro@gmail.com>, Trustcode
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import re
import pytz
import base64
from datetime import datetime
from odoo import api, fields, models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTFT
from pytrustnfe.nfe import autorizar_nfe
from pytrustnfe.nfe import retorno_autorizar_nfe
from pytrustnfe.certificado import Certificado


class InvoiceEletronic(models.Model):
    _inherit = 'invoice.eletronic'

    ind_final = fields.Selection([
        ('0', u'Não'),
        ('1', u'Consumidor final')
    ], u'Operação com Consumidor final', readonly=True,
        states={'draft': [('readonly', False)]}, required=False,
        help=u'Indica operação com Consumidor final.', default='0')
    ind_pres = fields.Selection([
        ('0', u'Não se aplica'),
        ('1', u'Operação presencial'),
        ('2', u'Operação não presencial, pela Internet'),
        ('3', u'Operação não presencial, Teleatendimento'),
        ('4', u'NFC-e em operação com entrega em domicílio'),
        ('9', u'Operação não presencial, outros'),
    ], u'Tipo de operação', readonly=True,
        states={'draft': [('readonly', False)]}, required=False,
        help=u'Indicador de presença do comprador no\n'
             u'estabelecimento comercial no momento\n'
             u'da operação.', default='0')

    recibo_nfe = fields.Char(string="Recibo NFe", size=50)
    chave_nfe = fields.Char(string="Chave NFe", size=50)
    protocolo_nfe = fields.Char(string="Protocolo Autorização", size=50)

    def barcode_url(self):
        url = '<img style="width:470px;height:50px;margin-top:5px;"\
src="/report/barcode/Code128/' + self.chave_nfe + '" />'
        return url

    @api.multi
    def _hook_validation(self):
        errors = super(InvoiceEletronic, self)._hook_validation()
        if not self.ind_final:
            errors.append(u'Configure o indicador de consumidor final')
        if not self.fiscal_position_id:
            errors.append(u'Configure a posição fiscal')

        for eletr in self.eletronic_item_ids:
            prod = u"Produto: %s - %s" % (eletr.product_id.default_code,
                                          eletr.product_id.name)

            if not eletr.cfop:
                errors.append(u'%s - CFOP' % prod)
            if eletr.tipo_produto == 'product':
                if not eletr.icms_cst:
                    errors.append(u'%s - CST do ICMS' % prod)
                if not eletr.ipi_cst:
                    errors.append(u'%s - CST do IPI' % prod)
            if eletr.tipo_produto == 'service':
                if not eletr.issqn_codigo:
                    errors.append(u'%s - Código de Serviço' % prod)
            if not eletr.pis_cst:
                errors.append(u'%s - CST do PIS' % prod)
            if not eletr.cofins_cst:
                errors.append(u'%s - CST do Cofins' % prod)

        return errors

    @api.one
    def _id_dest(self):
        id_dest = '1'
        if self.company_id.state_id != self.partner_id.state_id:
            id_dest = '2'
        if self.company_id.country_id != self.partner_id.country_id:
            id_dest = '3'
        return id_dest

    @api.multi
    def _prepare_eletronic_invoice_item(self, item, invoice):
        prod = {
            'cProd': item.product_id.default_code,
            'cEAN': item.product_id.barcode or '',
            'xProd': item.product_id.name,
            'NCM': '39259090',
            'CFOP': item.cfop,
            'uCom': item.uom_id.name,
            'qCom': item.quantidade,
            'vUnCom': item.preco_unitario,
            'vProd':  "%.02f" % item.valor_liquido,
            'cEANTrib': item.product_id.barcode or '',
            'uTrib': item.uom_id.name,
            'qTrib': item.quantidade,
            'vUnTrib': item.preco_unitario,
            'indTot': item.indicador_total,
            'cfop': item.cfop
        }
        imposto = {
            'vTotTrib': "%.02f" % item.tributos_estimados,
            'ICMS': {
                'orig':  item.origem,
                'CST': item.icms_cst,
                'modBC': item.icms_modalidade_BC,
                'vBC': "%.02f" % item.icms_base_calculo,
                'pICMS': "%.02f" % item.icms_aliquota,
                'vICMS': "%.02f" % item.icms_valor,
                'pCredSN': "%.02f" % item.icms_value_credit,
                'vCredICMSSN': "%.02f" % item.icms_value_percentual
            },
            'IPI': {
                'cEnq': 999,
                'CST': item.ipi_cst,
                'vBC': "%.02f" % item.ipi_base_calculo,
                'pIPI': "%.02f" % item.ipi_aliquota,
                'vIPI': "%.02f" % item.ipi_valor
            },
            'PIS': {
                'CST': item.pis_cst,
                'vBC': "%.02f" % item.pis_base_calculo,
                'pPIS': "%.02f" % item.pis_aliquota,
                'vPIS': "%.02f" % item.pis_valor
            },
            'COFINS': {
                'CST': item.cofins_cst,
                'vBC': "%.02f" % item.cofins_base_calculo,
                'pCOFINS': "%.02f" % item.cofins_aliquota,
                'vCOFINS': "%.02f" % item.cofins_valor
            }
        }
        return {'prod': prod, 'imposto': imposto}

    @api.multi
    def _prepare_eletronic_invoice_values(self):
        tz = pytz.timezone(self.env.user.partner_id.tz) or pytz.utc
        dt_emissao = datetime.strptime(self.data_emissao, DTFT)
        dt_emissao = pytz.utc.localize(dt_emissao).astimezone(tz)

        ide = {
            'cUF': self.company_id.state_id.ibge_code,
            'cNF': "%08d" % self.numero_controle,
            'natOp': self.fiscal_position_id.name,
            'indPag': self.payment_term_id.indPag or '0',
            'mod': self.model,
            'serie': self.serie.code,
            'nNF': self.numero,
            'dhEmi': dt_emissao.strftime('%Y-%m-%dT%H:%M:%S-03:00'),
            'dhSaiEnt': dt_emissao.strftime('%Y-%m-%dT%H:%M:%S-03:00'),
            'tpNF': self.finalidade_emissao,
            'idDest': self._id_dest()[0],
            'cMunFG': "%s%s" % (self.company_id.state_id.ibge_code,
                                self.company_id.city_id.ibge_code),
            # Formato de Impressão do DANFE - 1 - Danfe Retrato, 4 - Danfe NFCe
            'tpImp': '1' if self.model == '55' else '4',
            'tpEmis': 1,  # Tipo de Emissão da NF-e - 1 - Emissão Normal
            'tpAmb': 2 if self.ambiente == 'homologacao' else 1,
            'finNFe': self.finalidade_emissao,
            'indFinal': self.ind_final,
            'indPres': self.ind_pres,
            'procEmi': 0
        }
        emit = {
            'tipo': self.company_id.partner_id.company_type,
            'cnpj_cpf': re.sub('[^0-9]', '', self.company_id.cnpj_cpf),
            'xNome': self.company_id.legal_name,
            'xFant': self.company_id.name,
            'enderEmit': {
                'xLgr': self.company_id.street,
                'nro': self.company_id.number,
                'xBairro': self.company_id.district,
                'cMun': '%s%s' % (
                    self.company_id.partner_id.state_id.ibge_code,
                    self.company_id.partner_id.city_id.ibge_code),
                'xMun': self.company_id.city_id.name,
                'UF': self.company_id.state_id.code,
                'CEP': re.sub('[^0-9]', '', self.company_id.zip),
                'cPais': self.company_id.country_id.ibge_code,
                'xPais': self.company_id.country_id.name,
                'fone': re.sub('[^0-9]', '', self.company_id.phone or '')
            },
            'IE':  re.sub('[^0-9]', '', self.company_id.inscr_est),
            'CRT': self.company_id.fiscal_type,
        }
        ind_ie_dest = False
        if self.partner_id.is_company:
            if self.partner_id.inscr_est:
                ind_ie_dest = '1'
            elif self.partner_id.state_id.code in ('AM', 'BA', 'CE', 'GO',
                                                   'MG', 'MS', 'MT', 'PE',
                                                   'RN', 'SP'):
                ind_ie_dest = '9'
            else:
                ind_ie_dest = '2'
        else:
            ind_ie_dest = '9'
        if self.partner_id.indicador_ie_dest:
            ind_ie_dest = self.partner_id.indicador_ie_dest

        dest = {
            'tipo': self.partner_id.company_type,
            'cnpj_cpf': re.sub('[^0-9]', '', self.partner_id.cnpj_cpf),
            'xNome': self.partner_id.legal_name or self.partner_id.name,
            'enderDest': {
                'xLgr': self.partner_id.street,
                'nro': self.partner_id.number,
                'xBairro': self.partner_id.district,
                'cMun': '%s%s' % (self.partner_id.state_id.ibge_code,
                                  self.partner_id.city_id.ibge_code),
                'xMun': self.partner_id.city_id.name,
                'UF': self.partner_id.state_id.code,
                'CEP': re.sub('[^0-9]', '', self.partner_id.zip),
                'cPais': self.partner_id.country_id.ibge_code,
                'xPais': self.partner_id.country_id.name,
                'fone': re.sub('[^0-9]', '', self.partner_id.phone or '')
            },
            'indIEDest': ind_ie_dest,
            'IE':  re.sub('[^0-9]', '', self.partner_id.inscr_est or ''),
        }
        if self.ambiente == 'homologacao':
            dest['xNome'] = \
                'NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL'
        if self.partner_id.country_id.id != self.company_id.country_id.id:
            dest['enderDest']['UF'] = 'EX'
            dest['enderDest']['xMun'] = 'Exterior'
            dest['enderDest']['cMun'] = '9999999'

        eletronic_items = []
        for item in self.eletronic_item_ids:
            eletronic_items.append(
                self._prepare_eletronic_invoice_item(item, self))
        total = {
            # ICMS
            'vBC': "%.02f" % self.valor_bc_icms,
            'vICMS': "%.02f" % self.valor_icms,
            'vICMSDeson': '0.00',
            'vBCST': "%.02f" % self.valor_bc_icmsst,
            'vST': "%.02f" % self.valor_icmsst,
            'vProd': "%.02f" % self.valor_bruto,
            'vFrete': "%.02f" % self.valor_frete,
            'vSeg': "%.02f" % self.valor_seguro,
            'vDesc': '0.00',
            'vII': "%.02f" % self.valor_ii,
            'vIPI': '0.00',
            'vPIS': '0.00',
            'vCOFINS': '0.00',
            'vOutro': "%.02f" % self.valor_despesas,
            'vNF': "%.02f" % self.valor_final,
            'vTotTrib': '0.00',
            #ISSQn
            'vServ': '0.00',
            #Retenções

        }
        transp = {
            'modFrete': 9
        }
        vencimento = fields.Datetime.from_string(self.data_emissao)
        cobr = {
            'dup': [{
                'nDup': '1',
                'dVenc': vencimento.strftime('%Y-%m-%d'),
                'vDup': "%.02f" % self.valor_final
            }]
        }
        infAdic = {
            'infCpl': self.informacoes_complementares,
            'infAdFisco': self.informacoes_legais,
        }
        vals = {
            'Id': '',
            'ide': ide,
            'emit': emit,
            'dest': dest,
            'detalhes': eletronic_items,
            'total': total,
            'transp': transp,
            'cobr': cobr,
            'infAdic': infAdic,
        }
        return vals

    @api.multi
    def _prepare_lote(self, lote, nfe_values):
        return {
            'idLote': lote,
            'indSinc': 1,
            'estado': self.company_id.partner_id.state_id.ibge_code,
            'ambiente': 1 if self.ambiente == 'producao' else 2,
            'NFes': [{
                'infNFe': nfe_values
            }]
        }

    def _create_attachment(self, event, data):
        file_name = 'nfe-%s.xml' % datetime.now().strftime('%Y-%m-%d-%H-%M')
        self.env['ir.attachment'].create(
            {
                'name': file_name,
                'datas': base64.b64encode(data),
                'datas_fname': file_name,
                'description': u'',
                'res_model': 'invoice.eletronic',
                'res_id': event.id
            })

    @api.multi
    def action_send_eletronic_invoice(self):
        self.ambiente = 'homologacao'  # Evita esquecimentos
        super(InvoiceEletronic, self).action_send_eletronic_invoice()

        nfe_values = self._prepare_eletronic_invoice_values()
        lote = self._prepare_lote(1, nfe_values)
        cert = self.company_id.with_context({'bin_size': False}).nfe_a1_file
        cert_pfx = base64.decodestring(cert)

        certificado = Certificado(cert_pfx, self.company_id.nfe_a1_password)

        resposta_recibo = None
        resposta = autorizar_nfe(certificado, **lote)
        retorno = resposta['object'].Body.nfeAutorizacaoLoteResult.retEnviNFe
        if retorno.cStat == 103:
            obj = {
                'estado': self.company_id.partner_id.state_id.ibge_code,
                'ambiente': 1 if self.ambiente == 'producao' else 2,
                'obj': {
                    'ambiente': 1 if self.ambiente == 'producao' else 2,
                    'numero_recibo': retorno.infRec.nRec
                }
            }
            self.recibo_nfe = obj['obj']['numero_recibo']
            import time
            time.sleep(2)
            resposta_recibo = retorno_autorizar_nfe(certificado, **obj)
            retorno = resposta_recibo['object'].Body.\
                nfeRetAutorizacaoLoteResult.retConsReciNFe

        if retorno.cStat != 104:
            self.codigo_retorno = retorno.cStat
            self.mensagem_retorno = retorno.xMotivo
        else:
            self.codigo_retorno = retorno.protNFe.infProt.cStat
            self.mensagem_retorno = retorno.protNFe.infProt.xMotivo
            if self.codigo_retorno == '100':
                self.write({'state': 'done'})
            # Duplicidade de NF-e significa que a nota já está emitida
            # TODO Buscar o protocolo de autorização, por hora só finalizar
            if self.codigo_retorno == '204':
                self.write({'state': 'done', 'codigo_retorno': '100',
                            'mensagem_retorno': 'Autorizado o uso da NF-e'})

        self.env['invoice.eletronic.event'].create({
            'code': self.codigo_retorno,
            'name': self.mensagem_retorno,
            'invoice_eletronic_id': self.id,
        })
        self._create_attachment(self, resposta['sent_xml'])
        self._create_attachment(self, resposta['received_xml'])
        if resposta_recibo:
            self._create_attachment(self, resposta_recibo['sent_xml'])
            self._create_attachment(self, resposta_recibo['received_xml'])