module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n12,
    n11,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n6;
  output n7;
  output n8;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test77/test77.v:29$12_Y , \$and$testcase/test77/test77.v:33$17_Y , ___xor_testcase_test77_test77_v_19_3_n1_0__, ___xor_testcase_test77_test77_v_19_3_n2_0__, ___xor_testcase_test77_test77_v_19_3_n3_0__, ___xor_testcase_test77_test77_v_24_7_n1_0__, ___xor_testcase_test77_test77_v_24_7_n2_0__, ___xor_testcase_test77_test77_v_24_7_n3_0__, ___xor_testcase_test77_test77_v_28_11_n1_0__, ___xor_testcase_test77_test77_v_28_11_n2_0__, ___xor_testcase_test77_test77_v_28_11_n3_0__, ___xor_testcase_test77_test77_v_32_16_n1_0__, ___xor_testcase_test77_test77_v_32_16_n2_0__, ___xor_testcase_test77_test77_v_32_16_n3_0__, ___xor_testcase_test77_test77_v_47_26_n1_0__, ___xor_testcase_test77_test77_v_47_26_n2_0__, ___xor_testcase_test77_test77_v_47_26_n3_0__, n13, n14, n15, n16, n17, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test77/test77.v:48$27  (n80, n2[0], n2[1]);
  and   \$and$testcase/test77/test77.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test77/test77.v:23$6  (n30, n2[1], n3[1]);
  and   \$and$testcase/test77/test77.v:26$9  (n40, n2[2], n3[2]);
  and   \$and$testcase/test77/test77.v:30$14  (n44, n2[2], n3[2]);
  nand  xor_nand_6 (___xor_testcase_test77_test77_v_28_11_n1_0__, n2[2], n3[2]);
  nand  xor_nand_9 (___xor_testcase_test77_test77_v_32_16_n1_0__, n2[2], n3[2]);
  not   \$not$testcase/test77/test77.v:42$31  (n63, n4);
  and   \$and$testcase/test77/test77.v:29$12  (\$and$testcase/test77/test77.v:29$12_Y , n2[2], n5);
  and   \$and$testcase/test77/test77.v:33$17  (\$and$testcase/test77/test77.v:33$17_Y , n2[2], n5);
  or    \$or$testcase/test77/test77.v:27$10  (n41, n2[2], n5);
  or    \$or$testcase/test77/test77.v:31$15  (n45, n2[2], n5);
  or    \$or$testcase/test77/test77.v:49$28  (n81, n80, n3[0]);
  or    \$or$testcase/test77/test77.v:17$2  (n14, n13, n4);
  nand  xor_nand_3 (___xor_testcase_test77_test77_v_24_7_n1_0__, n30, n5);
  nand  xor_nand_7 (___xor_testcase_test77_test77_v_28_11_n2_0__, n2[2], ___xor_testcase_test77_test77_v_28_11_n1_0__);
  nand  xor_nand_8 (___xor_testcase_test77_test77_v_28_11_n3_0__, n3[2], ___xor_testcase_test77_test77_v_28_11_n1_0__);
  nand  xor_nand_10 (___xor_testcase_test77_test77_v_32_16_n2_0__, n2[2], ___xor_testcase_test77_test77_v_32_16_n1_0__);
  nand  xor_nand_11 (___xor_testcase_test77_test77_v_32_16_n3_0__, n3[2], ___xor_testcase_test77_test77_v_32_16_n1_0__);
  not   \$not$testcase/test77/test77.v:29$13  (n43, \$and$testcase/test77/test77.v:29$12_Y );
  not   \$not$testcase/test77/test77.v:33$18  (n47, \$and$testcase/test77/test77.v:33$17_Y );
  or    \$or$testcase/test77/test77.v:34$19  (n56, n40, n41);
  not   \$not$testcase/test77/test77.v:50$33  (n82, n81);
  not   \$not$testcase/test77/test77.v:18$29  (n15, n14);
  nand  xor_nand_4 (___xor_testcase_test77_test77_v_24_7_n2_0__, n30, ___xor_testcase_test77_test77_v_24_7_n1_0__);
  nand  xor_nand_5 (___xor_testcase_test77_test77_v_24_7_n3_0__, n5, ___xor_testcase_test77_test77_v_24_7_n1_0__);
  nand  \$xor$testcase/test77/test77.v:28$11  (n42, ___xor_testcase_test77_test77_v_28_11_n2_0__, ___xor_testcase_test77_test77_v_28_11_n3_0__);
  nand  \$xor$testcase/test77/test77.v:32$16  (n46, ___xor_testcase_test77_test77_v_32_16_n2_0__, ___xor_testcase_test77_test77_v_32_16_n3_0__);
  nand  xor_nand_0 (___xor_testcase_test77_test77_v_19_3_n1_0__, n15, n5);
  nand  \$xor$testcase/test77/test77.v:24$7  (n31, ___xor_testcase_test77_test77_v_24_7_n2_0__, ___xor_testcase_test77_test77_v_24_7_n3_0__);
  or    \$or$testcase/test77/test77.v:35$20  (n57, n56, n42);
  nand  xor_nand_1 (___xor_testcase_test77_test77_v_19_3_n2_0__, n15, ___xor_testcase_test77_test77_v_19_3_n1_0__);
  nand  xor_nand_2 (___xor_testcase_test77_test77_v_19_3_n3_0__, n5, ___xor_testcase_test77_test77_v_19_3_n1_0__);
  or    \$or$testcase/test77/test77.v:25$8  (n7, n31, n4);
  or    \$or$testcase/test77/test77.v:36$21  (n8, n57, n43);
  nand  \$xor$testcase/test77/test77.v:19$3  (n16, ___xor_testcase_test77_test77_v_19_3_n2_0__, ___xor_testcase_test77_test77_v_19_3_n3_0__);
  and   \$and$testcase/test77/test77.v:45$25  (n65, n31, n8);
  and   \$and$testcase/test77/test77.v:20$4  (n17, n16, n2[1]);
  dff g30 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  or    \$or$testcase/test77/test77.v:21$5  (n6, n17, n3[1]);
  nand  xor_nand_12 (___xor_testcase_test77_test77_v_47_26_n1_0__, n6, n7);
  nand  xor_nand_13 (___xor_testcase_test77_test77_v_47_26_n2_0__, n6, ___xor_testcase_test77_test77_v_47_26_n1_0__);
  nand  xor_nand_14 (___xor_testcase_test77_test77_v_47_26_n3_0__, n7, ___xor_testcase_test77_test77_v_47_26_n1_0__);
  nand  \$xor$testcase/test77/test77.v:47$26  (n12, ___xor_testcase_test77_test77_v_47_26_n2_0__, ___xor_testcase_test77_test77_v_47_26_n3_0__);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
